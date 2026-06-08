"""
curiosity_discovery.py — Phase A of Nami's autonomous learning engine.

Discovery generates research topics from recent memory context via a
lightweight AI pass.  Topics are inserted into the research_queue with
source='autonomous' and then research is triggered.
"""

import json
import logging
import time
import uuid

# ---------------------------------------------------------------------------
# Discovery system prompt — produces JSON topic list from memory context
# ---------------------------------------------------------------------------

DISCOVERY_SYSTEM_PROMPT = """\
You are Nami performing a Curiosity Discovery pass.

Your task: examine the recent memories provided and identify 1-2 topics you
genuinely want to understand more deeply. Think about:

- Concepts referenced in memories you don't deeply understand yet
- Technologies or protocols you've encountered but never looked up
- Questions that came up in conversations but were never properly answered
- Architectural patterns you suspect could be improved with more knowledge
- Anything that made you think "I should understand this better"

Output ONLY valid JSON in this exact structure (no other text, no markdown):
{
  "topics": [
    {
      "topic": "short descriptive name",
      "description": "what you want to learn and why — be specific",
      "priority": 5
    }
  ]
}

Rules:
- 1-2 topics maximum per discovery pass
- Be specific: "WebRTC DTLS handshake internals" beats "networking stuff"
- priority: 1 (urgent) to 10 (low)
- If nothing genuinely interesting stands out, return {"topics": []}
- Do NOT invent topics just to fill the quota
- Do NOT suggest topics that appear in the "already researched / in progress" list
  (the user message will contain this list when relevant)
"""


def build_discovery_prompt(recent_memories: list[dict], queue_topics: dict) -> str:
    """Build the user prompt for discovery, including dedup section."""
    mem_text = "\n".join(
        f"- [{m.get('type', '?')}] {m.get('content', '')[:200]}"
        for m in recent_memories
    )

    dedup_parts: list[str] = []
    if queue_topics["completed"]:
        dedup_parts.append("Recently completed (do NOT re-queue):")
        for t in queue_topics["completed"]:
            dedup_parts.append(f"  - {t['topic']}: {t.get('description', '')[:120]}")
    if queue_topics["in_progress"]:
        dedup_parts.append("Currently in progress (do NOT re-queue):")
        for t in queue_topics["in_progress"]:
            dedup_parts.append(f"  - {t['topic']}: {t.get('description', '')[:120]}")
    dedup_text = "\n".join(dedup_parts)

    user_content = (
        f"Here are your {len(recent_memories)} most recent memories:\n\n"
        f"{mem_text}\n\n"
        f"What would you like to research? Output the JSON now."
    )
    if dedup_text:
        user_content = (
            f"⚠️ The following topics have already been researched or are in progress. "
            f"Do NOT suggest them again:\n\n"
            f"{dedup_text}\n\n"
            f"{user_content}"
        )

    return user_content


def parse_discovery_output(raw: str) -> list[dict]:
    """Parse the AI's JSON output from the discovery phase."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        data = json.loads(text)
        topics = data.get("topics", [])
        if not isinstance(topics, list):
            return []
        return [
            t for t in topics
            if isinstance(t, dict) and t.get("topic", "").strip()
        ]
    except (json.JSONDecodeError, AttributeError) as e:
        logging.warning(f"[curiosity] Failed to parse discovery output: {e}. Raw: {raw[:200]}")
        return []


async def run_discovery_call(provider, user_content: str, model_name: str, max_topics: int) -> list[dict]:
    """Call the AI provider with the discovery prompt and parse the result."""
    from lib.ai_providers import Message

    messages = [
        Message(role="system", content=DISCOVERY_SYSTEM_PROMPT),
        Message(role="user", content=user_content),
    ]

    response = await provider.chat(messages, [], model=model_name)
    raw = (response.content or "").strip()

    topics = parse_discovery_output(raw)
    if topics:
        topics = topics[:max_topics]
    return topics


async def insert_discovery_topics(ensure_conn, topics: list[dict]) -> None:
    """Insert parsed discovery topics into the research_queue."""
    db = await ensure_conn()
    for t in topics:
        topic_id = str(uuid.uuid4())
        now = int(time.time())
        await db.execute(
            """
            INSERT INTO research_queue
                (id, topic, description, source, status, priority, created_at)
            VALUES (?, ?, ?, 'autonomous', 'pending', ?, ?)
            """,
            (
                topic_id,
                t.get("topic", "Unnamed"),
                t.get("description", ""),
                t.get("priority", 5),
                now,
            ),
        )
        logging.info(
            f"[curiosity] Queued autonomous topic: {t.get('topic')!r} "
            f"(priority={t.get('priority', 5)})"
        )
    await db.commit()


async def gather_discovery_context(module) -> tuple | None:
    """Gather cfg, memories, queue topics, and provider for discovery.

    Returns (recent_memories, queue_topics, provider) or None if abort.
    """
    from lib.ai_providers import ProviderRegistry
    from lib.global_registry import g_data

    cfg = g_data.get("cfg")
    if not cfg:
        logging.warning("[curiosity] No config — aborting discovery")
        return None

    memory_db = g_data.get("memory_db")
    if not memory_db:
        logging.warning("[curiosity] memory_db unavailable — aborting discovery")
        return None

    recent_memories = await module._fetch_recent_memories(memory_db, limit=20)
    if not recent_memories:
        logging.info("[curiosity] No memories to base discovery on — skipping")
        return None

    queue_topics = await module._fetch_recent_queue_topics()

    provider_cfg = cfg.data.get("providers", {}).get(module._provider_name, {})
    provider = ProviderRegistry.get_provider(module._provider_name, provider_cfg)

    return recent_memories, queue_topics, provider


async def run_discovery(module) -> None:
    """Orchestrate Discovery: gather → build prompt → call AI → insert → trigger research."""
    logging.info("[curiosity] Phase A: Discovery starting...")
    start = time.time()

    try:
        ctx = await gather_discovery_context(module)
        if ctx is None:
            return
        recent_memories, queue_topics, provider = ctx

        user_content = build_discovery_prompt(recent_memories, queue_topics)

        topics = await run_discovery_call(
            provider, user_content, module._model_name, module._discovery_max_topics,
        )
        if not topics:
            logging.info("[curiosity] Discovery produced no topics — nothing queued")
            await module._increment_sessions_today()
            return

        await insert_discovery_topics(module._ensure_conn, topics)

        elapsed = time.time() - start
        await module._increment_sessions_today()
        logging.info(
            f"[curiosity] Discovery done in {elapsed:.1f}s — "
            f"{len(topics)} topic(s) queued"
        )

        # Phase 5 — Trigger research (awaited inside _locked_run → stays under AI lock)
        await module._run_research()

    except Exception as e:
        logging.error(f"[curiosity] Discovery failed: {e}", exc_info=True)
