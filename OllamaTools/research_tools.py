"""
research_tools.py — Queue-management tools exclusively for the Research Agent.

These tools are NOT loaded during normal startup (dynamic_loader skips files
prefixed with 'research_'). They are injected only by the CuriosityModule when
spawning the Research Agent.

The Research Agent also receives all normal tools (search_web, mcp_playwright_browser_*,
run_bash, sandbox_*, memory tools, send_message, etc.) so it can fully research
topics on the web and test things in the sandbox. These tools here handle only
the research queue lifecycle.
"""

import json
import logging
import time

import aiosqlite

from lib.global_registry import g_data
from OllamaTools import _db_path

# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

async def research_get_queue(status: str = "pending") -> str:
    """
    List research topics in the queue.

    Args:
        status: Filter by status: 'pending', 'in_progress', 'done', 'failed', or 'all'.

    Returns:
        JSON list of research topic dicts.
    """
    path = _db_path()
    try:
        async with aiosqlite.connect(path) as db:
            if status == "all":
                async with db.execute(
                    "SELECT * FROM research_queue ORDER BY priority ASC, created_at ASC"
                ) as cur:
                    rows = await cur.fetchall()
                    cols = [d[0] for d in cur.description]
            else:
                async with db.execute(
                    "SELECT * FROM research_queue WHERE status = ? ORDER BY priority ASC, created_at ASC",
                    (status,),
                ) as cur:
                    rows = await cur.fetchall()
                    cols = [d[0] for d in cur.description]

        results = [dict(zip(cols, row)) for row in rows]
        return json.dumps(results, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


async def research_start_topic(topic_id: str) -> str:
    """
    Mark a research topic as in-progress and return its full details.

    Call this before starting research on a topic to claim it.

    Args:
        topic_id: UUID of the research topic.

    Returns:
        JSON dict with the topic details, or error string.
    """
    path = _db_path()
    try:
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "UPDATE research_queue SET status = 'in_progress' WHERE id = ? AND status = 'pending'",
                (topic_id,),
            )
            await db.commit()
            async with db.execute(
                "SELECT * FROM research_queue WHERE id = ?", (topic_id,)
            ) as cur:
                row = await cur.fetchone()
                cols = [d[0] for d in cur.description]

        if not row:
            return json.dumps({"error": f"Topic {topic_id} not found"})
        return json.dumps(dict(zip(cols, row)), default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


async def research_store_finding(
    topic_id: str,
    finding: str,
    source_url: str = "",
) -> str:
    """
    Store a research finding as a KnowledgeUnit in Nami's memory graph.

    Call this for each important insight discovered during research.
    Multiple findings per topic is fine — store granular facts separately.

    Args:
        topic_id:   UUID of the research topic this finding belongs to.
        finding:    Concise, factual statement of what was learned.
                    Write as if storing for a future conversation: precise, actionable.
        source_url: Optional URL where this was found.

    Returns:
        Confirmation string.
    """
    memory_db = g_data.get("memory_db")
    if not memory_db:
        return json.dumps({"error": "memory_db not available"})

    # Look up the topic name for richer context
    topic_name = topic_id
    try:
        path = _db_path()
        async with aiosqlite.connect(path) as db:
            async with db.execute(
                "SELECT topic FROM research_queue WHERE id = ?", (topic_id,)
            ) as cur:
                row = await cur.fetchone()
                if row:
                    topic_name = row[0]
    except aiosqlite.OperationalError:
        pass

    memory_args = {
        "statement": finding,
        "type": "research_finding",
        "source": source_url or f"research:{topic_name}",
        "confidenceScore": 0.8,
    }

    try:
        await memory_db.add_memory(
            user_id="nami",
            user_name="Nami",
            memory_type="KnowledgeUnit",
            memory_args=memory_args,
        )
        logging.info(f"[research] Stored finding for topic={topic_name!r}: {finding[:80]}")
        return json.dumps({"stored": True, "topic": topic_name, "finding": finding[:120]})
    except Exception as e:
        logging.error(f"[research] Failed to store finding: {e}", exc_info=True)
        return json.dumps({"error": str(e)})


async def research_complete_topic(topic_id: str, summary: str) -> str:
    """
    Mark a research topic as done and store the final summary.

    Call this after you have finished researching a topic and stored all
    relevant findings.  Fires a ``task.completed`` event so the next chat turn
    surfaces this research to Nami.

    Args:
        topic_id: UUID of the research topic.
        summary:  Brief summary of what was learned overall.

    Returns:
        Confirmation string.
    """
    path = _db_path()
    try:
        # Fetch the human-readable topic name before updating
        topic_name = topic_id
        async with aiosqlite.connect(path) as db:
            async with db.execute(
                "SELECT topic FROM research_queue WHERE id = ?", (topic_id,)
            ) as cur:
                row = await cur.fetchone()
                if row:
                    topic_name = row[0]

            await db.execute(
                "UPDATE research_queue SET status = 'done', result = ? WHERE id = ?",
                (summary, topic_id),
            )
            await db.commit()

        logging.info(f"[research] Topic {topic_id!r} ({topic_name!r}) completed. Summary: {summary[:100]}")

        # Notify the chat context so Nami knows about her research when next spoken to,
        # and reset idle timers (dream/curiosity/heartbeat) with a 30-min cooldown.
        event_bus = g_data.get("event_bus")
        if event_bus:
            from lib.services.event_bus import Event
            await event_bus.publish(Event("task.completed", {
                "task_type": "research",
                "title": topic_name,
                "summary": summary,
            }))
            await event_bus.publish(Event("activity.recorded", {}))

        return json.dumps({"completed": True, "topic_id": topic_id})
    except Exception as e:
        return json.dumps({"error": str(e)})


async def research_fail_topic(topic_id: str, reason: str) -> str:
    """
    Mark a research topic as failed so it can be retried or skipped.

    On first failure the topic is set to ``failed_retry`` with an exponential
    backoff.  The CuriosityModule will promote it back to ``pending`` when the
    backoff expires.  After too many failures it is permanently marked ``failed``.

    Args:
        topic_id: UUID of the research topic.
        reason:   Why the research failed (rate limit, topic too broad, etc.).

    Returns:
        Confirmation string.
    """
    path = _db_path()
    try:
        cfg = g_data.get("cfg")
        max_retries = 3
        backoff_base_hours = 1
        if cfg:
            hb_cfg = cfg.data.get("heartbeat", {})
            mod_cfg = hb_cfg.get("modules", {}).get("curiosity", {})
            max_retries = mod_cfg.get("retry_max_attempts", 3)
            backoff_base_hours = mod_cfg.get("retry_backoff_base_hours", 1)

        async with aiosqlite.connect(path) as db:
            # Get current retry count
            async with db.execute(
                "SELECT retry_count FROM research_queue WHERE id = ?", (topic_id,)
            ) as cur:
                row = await cur.fetchone()
            current_retries = (row[0] or 0) if row else 0

            if current_retries >= max_retries:
                # Permanently failed — no more retries
                await db.execute(
                    "UPDATE research_queue SET status = 'failed', result = ? WHERE id = ?",
                    (f"FAILED (max retries): {reason}", topic_id),
                )
                await db.commit()
                logging.warning(
                    f"[research] Topic {topic_id} permanently failed after "
                    f"{current_retries} retries: {reason}"
                )
                return json.dumps({
                    "failed": True, "permanent": True,
                    "topic_id": topic_id, "reason": reason,
                    "retries": current_retries,
                })
            else:
                # Schedule retry with exponential backoff
                new_retry_count = current_retries + 1
                backoff_seconds = backoff_base_hours * 3600 * (4 ** (new_retry_count - 1))
                next_retry_at = int(time.time()) + backoff_seconds
                await db.execute(
                    "UPDATE research_queue SET status = 'failed_retry', "
                    "retry_count = ?, next_retry_at = ?, result = ? "
                    "WHERE id = ?",
                    (new_retry_count, next_retry_at, f"FAILED: {reason}", topic_id),
                )
                await db.commit()
                backoff_str = f"{backoff_seconds // 3600}h{(backoff_seconds % 3600) // 60}m"
                logging.warning(
                    f"[research] Topic {topic_id} failed (retry {new_retry_count}/{max_retries}): "
                    f"{reason} — backoff={backoff_str}"
                )
                return json.dumps({
                    "failed": True, "retry_scheduled": True,
                    "topic_id": topic_id, "reason": reason,
                    "retry_count": new_retry_count, "max_retries": max_retries,
                    "backoff": backoff_str,
                })
    except Exception as e:
        return json.dumps({"error": str(e)})


async def research_search_memory(query: str, limit: int = 5) -> str:
    """
    Search Nami's memory graph for what she already knows about a topic.

    Use this before researching a topic to avoid duplicating existing knowledge.
    Focus your research on what's missing or outdated.

    Args:
        query: Natural language query describing the topic.
        limit: Max results (default 5).

    Returns:
        JSON list of {score, statement/summary, type} dicts.
    """
    memory_db = g_data.get("memory_db")
    if not memory_db:
        return json.dumps({"error": "memory_db not available"})

    try:
        results = await memory_db.search(query=query, top_k=limit)
        output = []
        for mem_obj, score in results:
            d = mem_obj.to_dict() if hasattr(mem_obj, "to_dict") else vars(mem_obj)
            d.pop("summaryEmbeddingVector", None)
            output.append({
                "score": round(score, 4),
                "type": type(mem_obj).__name__,
                "memory": d,
            })
        return json.dumps(output, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

def get_tool() -> list[dict]:
    """
    Return all research-agent tools as a list.

    Injected by CuriosityModule, never loaded by the normal ToolLoader
    (which skips files starting with 'research_').
    """
    return [
        {
            "type": "function",
            "safe": True,
            "categories": ["research_queue"],
            "function": {
                "name": "research_get_queue",
                "description": "List topics in the research queue. Use at the start of a session to see what needs to be done.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "description": "'pending', 'in_progress', 'done', 'failed', 'failed_retry', or 'all'. Default: 'pending'.",
                        }
                    },
                    "required": [],
                },
            },
            "func": research_get_queue,
        },
        {
            "type": "function",
            "safe": True,
            "categories": ["research_queue"],
            "function": {
                "name": "research_start_topic",
                "description": "Claim a pending topic and get its full details before starting research.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic_id": {"type": "string", "description": "UUID of the research topic."},
                    },
                    "required": ["topic_id"],
                },
            },
            "func": research_start_topic,
        },
        {
            "type": "function",
            "safe": False,
            "categories": ["research_queue"],
            "function": {
                "name": "research_store_finding",
                "description": (
                    "Store a research finding as a KnowledgeUnit in memory. "
                    "Call for each distinct fact or insight. Be concise and precise."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic_id": {"type": "string", "description": "UUID of the research topic."},
                        "finding": {
                            "type": "string",
                            "description": "Concise factual statement of what was learned.",
                        },
                        "source_url": {
                            "type": "string",
                            "description": "Optional URL source for this finding.",
                        },
                    },
                    "required": ["topic_id", "finding"],
                },
            },
            "func": research_store_finding,
        },
        {
            "type": "function",
            "safe": False,
            "categories": ["research_queue"],
            "function": {
                "name": "research_complete_topic",
                "description": "Mark a topic as done after storing all findings. Include a brief overall summary.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic_id": {"type": "string", "description": "UUID of the research topic."},
                        "summary": {"type": "string", "description": "Brief overall summary of what was learned."},
                    },
                    "required": ["topic_id", "summary"],
                },
            },
            "func": research_complete_topic,
        },
        {
            "type": "function",
            "safe": False,
            "categories": ["research_queue"],
            "function": {
                "name": "research_fail_topic",
                "description": "Mark a topic as failed (e.g. too broad, rate-limited, inconclusive). It can be retried.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic_id": {"type": "string", "description": "UUID of the research topic."},
                        "reason": {"type": "string", "description": "Why research failed."},
                    },
                    "required": ["topic_id", "reason"],
                },
            },
            "func": research_fail_topic,
        },
        {
            "type": "function",
            "safe": True,
            "categories": ["research_queue"],
            "function": {
                "name": "research_search_memory",
                "description": (
                    "Search existing memory for what Nami already knows about a topic. "
                    "Call before researching to avoid re-learning what's already known."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Natural language query."},
                        "limit": {"type": "integer", "description": "Max results (default 5)."},
                    },
                    "required": ["query"],
                },
            },
            "func": research_search_memory,
        },
    ]
