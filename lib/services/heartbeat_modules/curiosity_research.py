"""
curiosity_research.py — Phase B of Nami's autonomous learning engine.

A full Research Agent with ALL tools (web search, sandbox, memory read/write,
send_message, etc.) investigates each pending topic, stores findings as
KnowledgeUnits in Neo4j, and decides on its own whether to message the owner.
"""

import logging
import time

# ---------------------------------------------------------------------------
# Research system prompt — full autonomy with all tools
# ---------------------------------------------------------------------------

RESEARCH_SYSTEM_PROMPT = """\
You are Nami performing a Curiosity Research session — autonomous, unsupervised learning.

⚠️  CRITICAL: Your text responses are DISCARDED. ONLY tool calls persist data.
    If you do not call research_store_finding, you have learned NOTHING.
    If you do not call research_complete_topic, your work is marked FAILED.

For EVERY topic in the queue, execute these steps IN ORDER using tool calls:

  STEP 1 → research_get_queue(status="pending")           — list pending topics
  STEP 2 → research_start_topic(topic_id)                  — claim the topic
  STEP 3 → research_search_memory(query)                   — check existing knowledge
  STEP 4 → search_web / mcp_playwright_browser_navigate + mcp_playwright_browser_snapshot (repeat as needed)  — gather information
  STEP 5 → research_store_finding(topic_id, finding, url)  — REQUIRED: call once per fact
             ↑ This is MANDATORY. At least 3 findings per topic. No findings = wasted session.
  STEP 6 → research_complete_topic(topic_id, summary)      — REQUIRED: marks topic done
             ↑ This is MANDATORY. Not calling this resets the topic to FAILED.

Rules:
- research_store_finding MUST be called before research_complete_topic
- Store practical, actionable knowledge — not vague summaries
- Multiple small precise findings > one big vague one
- Verify claims — don't trust a single source for contested facts
- If research hits a dead end, call research_fail_topic(topic_id, reason) to unblock
- Use send_message only if a finding is immediately actionable for {{owner}}

REMEMBER: The loop ends when you stop calling tools. Finish storing and completing BEFORE
writing any final text response.
"""


async def run_research_agent(module):
    """
    Setup provider/tools/MCP/context and execute the first research tool loop.

    Returns ``(response, messages, ctx, provider)``, or ``None`` if the config
    is missing.  Populates ``module._research_tools_called`` with every tool name
    the agent invoked.
    """
    from lib.ai_providers import Message, ProviderRegistry
    from lib.global_registry import g_data
    from lib.services.tool_executor import execute_tool_loop
    from lib.services.tool_context import ToolContext
    from lib.system_prompt_parser import NamiSystemPrompt
    from lib.utils.dynamic_loader import ToolLoader

    cfg = g_data.get("cfg")
    if not cfg:
        logging.warning("[curiosity] No config — aborting research")
        return None

    provider_cfg = cfg.data.get("providers", {}).get(module._provider_name, {})
    provider = ProviderRegistry.get_provider(module._provider_name, provider_cfg)

    loader = ToolLoader()
    all_tools = await loader.load_tools(exclude_prefixes=[])

    try:
        from lib.utils.mcp_loader import load_mcp_tools
        mcp_tools = await load_mcp_tools()
        all_tools.extend(mcp_tools)
    except Exception as mcp_err:
        logging.debug(f"[curiosity] MCP tools not loaded: {mcp_err}")

    ctx = ToolContext._from_tools(all_tools)

    messages = [
        Message(role="system", content=await NamiSystemPrompt(path="", prompt=RESEARCH_SYSTEM_PROMPT).get_prompt()),
        Message(role="user", content="Begin the research session. Start with research_get_queue."),
    ]

    response = await provider.chat(messages, ctx.schemas, model=module._model_name)

    if response.tool_calls:
        async def _track(name: str) -> None:
            module._research_tools_called.add(name)

        response, _tool_msgs = await execute_tool_loop(
            provider=provider,
            messages=messages,
            tools=ctx.tools,
            model=module._model_name,
            initial_response=response,
            max_calls=module._max_tool_calls,
            max_rounds=module._max_tool_rounds,
            on_tool_start=_track,
            use_inline_placeholders=True,
        )

    return response, messages, ctx, provider


async def recover_research_session(messages, response, ctx, provider, tools_called, module):
    """
    Handle incomplete research sessions via re-prompt + second tool loop.

    Case A: agent stored findings but forgot ``research_complete_topic``.
        → Re-prompt just to complete; findings are already saved.
    Case B: agent stored nothing AND never completed — dead session.
        → Re-prompt to store findings first, then complete.
    """
    from lib.ai_providers import Message
    from lib.services.tool_executor import execute_tool_loop

    stored_findings = "research_store_finding" in tools_called
    completed_topic = "research_complete_topic" in tools_called

    if not completed_topic:
        if stored_findings:
            logging.warning(
                "[curiosity] Research Agent stored findings but never called "
                "research_complete_topic — re-prompting to complete the topic."
            )
            recovery_nudge = (
                "⚠️ You stored findings but never called research_complete_topic. "
                "Please call research_complete_topic now with a brief summary of what you learned. "
                "If multiple topics are still in_progress, complete each one."
            )
        else:
            logging.warning(
                "[curiosity] Research Agent finished WITHOUT calling research_store_finding "
                "or research_complete_topic — re-prompting once to recover findings."
            )
            recovery_nudge = (
                "⚠️ You have not stored any findings and have not completed any topics. "
                "Your research will be LOST. Please call research_store_finding for each "
                "key fact you learned, then call research_complete_topic. "
                "If there was nothing to learn, call research_fail_topic with a reason."
            )

        recovery_messages = messages + [
            Message(role="assistant", content=response.content or ""),
            Message(role="user", content=recovery_nudge),
        ]
        response = await provider.chat(recovery_messages, ctx.schemas, model=module._model_name)
        if response.tool_calls:
            response, _ = await execute_tool_loop(
                provider=provider,
                messages=recovery_messages,
                tools=ctx.tools,
                model=module._model_name,
                initial_response=response,
                max_calls=10,
                max_rounds=module._max_tool_rounds,
                use_inline_placeholders=True,
            )

    return response


async def reset_stale_in_progress_topics(ensure_conn) -> None:
    """
    Reset any ``in_progress`` research topics back to ``pending`` for retry.

    Called from ``run_research()``'s ``finally`` block.  If the AI claimed a
    topic (research_start_topic → in_progress) but ran out of context or tool
    calls before calling research_complete_topic, the topic must go back to
    ``pending`` so the next curiosity session can pick it up.
    """
    try:
        db = await ensure_conn()
        async with db.execute(
            "SELECT count(*) FROM research_queue WHERE status = 'in_progress'"
        ) as cur:
            row = await cur.fetchone()
        count = row[0] if row else 0
        if count > 0:
            await db.execute(
                "UPDATE research_queue SET status = 'pending' "
                "WHERE status = 'in_progress'"
            )
            await db.commit()
            logging.warning(
                f"[curiosity] {count} in_progress topic(s) were reset to 'pending' "
                "— research session ended without calling research_complete_topic(); "
                "will retry next session"
            )
    except Exception as e:
        logging.warning(f"[curiosity] Could not reset stale in_progress topics: {e}")


async def run_research(module) -> None:
    """
    Orchestrate a full research session: setup → execute → recover → cleanup.

    A ``finally`` block guarantees that any topic left ``in_progress`` after
    the session ends (e.g. the AI hit the max-tool-call limit without calling
    ``research_complete_topic``) is reset to ``pending``.  Without this, Gate
    1.8 in DreamModule would block dreaming indefinitely.
    """
    logging.info("[curiosity] Phase B: Research Agent starting...")
    start = time.time()

    # Fresh tool-tracking set for this session
    module._research_tools_called: set[str] = set()

    try:
        result = await run_research_agent(module)
        if result is None:
            return  # No config — abort early (finally still runs)

        response, messages, ctx, provider = result

        response = await recover_research_session(
            messages, response, ctx, provider,
            module._research_tools_called, module,
        )

        elapsed = time.time() - start
        summary = (response.content or "").strip()
        await module._increment_sessions_today()
        logging.info(
            f"[curiosity] Research Agent done in {elapsed:.1f}s. "
            f"Summary: {summary[:200]}"
        )

    except Exception as e:
        logging.error(f"[curiosity] Research Agent failed: {e}", exc_info=True)

    finally:
        await reset_stale_in_progress_topics(module._ensure_conn)
