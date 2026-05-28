"""
queue_research.py — Queue a research topic for autonomous investigation.

Nami can call this during conversations to schedule topics for background
research. The CuriosityModule drains this queue when idle, running a
full Research Agent to investigate each topic.
"""

import logging
import time
import uuid

import aiosqlite

from lib.global_registry import g_data
from OllamaTools import tool_error, tool_success

_DEFAULT_DB = "scheduler.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS research_queue (
    id          TEXT PRIMARY KEY,
    topic       TEXT NOT NULL,
    description TEXT DEFAULT '',
    source      TEXT DEFAULT 'conversation',
    status      TEXT DEFAULT 'pending',
    priority    INTEGER DEFAULT 5,
    created_at  INTEGER NOT NULL,
    result      TEXT,
    retry_count INTEGER DEFAULT 0,
    next_retry_at INTEGER
)
"""


def _db_path() -> str:
    cfg = g_data.get("cfg")
    if cfg:
        return cfg.data.get("scheduler", {}).get("db_path", _DEFAULT_DB)
    return _DEFAULT_DB


async def _ensure_table(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(_CREATE_TABLE)
        await db.commit()


async def queue_research(topic: str, description: str = "", priority: int = 5) -> str:
    """
    Queue a research topic for Nami to investigate during idle time.

    Stores the topic in the research queue. The CuriosityModule picks it
    up when Nami is idle and runs a full Research Agent on it — web search,
    sandbox experiments, memory storage.

    Args:
        topic:       Short name for the research topic
                     (e.g. 'WebRTC signalling', 'CRDT data structures').
        description: What you want to learn. More detail = better research output.
        priority:    1 (urgent) to 10 (low). Default 5.

    Returns:
        Confirmation with topic ID and queue depth.
    """
    path = _db_path()
    priority = max(1, min(10, priority))

    try:
        await _ensure_table(path)
        topic_id = str(uuid.uuid4())
        now = int(time.time())

        async with aiosqlite.connect(path) as db:
            async with db.execute(
                "SELECT count(*) FROM research_queue WHERE status = 'pending'"
            ) as cur:
                row = await cur.fetchone()
                depth = row[0] if row else 0

            await db.execute(
                """
                INSERT INTO research_queue (id, topic, description, source, status, priority, created_at)
                VALUES (?, ?, ?, 'conversation', 'pending', ?, ?)
                """,
                (topic_id, topic, description, priority, now),
            )
            await db.commit()

        logging.info(f"[queue_research] Queued: {topic!r} id={topic_id} priority={priority}")
        return tool_success({
            "id": topic_id,
            "topic": topic,
            "queue_depth": depth + 1,
            "message": (
                f"Research topic queued (priority {priority}). "
                f"I'll investigate when idle. ({depth + 1} topic(s) pending)"
            ),
        })
    except Exception as e:
        logging.error(f"[queue_research] Error: {e}", exc_info=True)
        return tool_error(str(e), topic=topic)


def get_tool() -> list[dict]:
    return [{
        "type": "function",
        "safe": True,
        "categories": ["research"],
        "function": {
            "name": "queue_research",
            "description": (
                "Queue a research topic for autonomous investigation during idle time. "
                "Use this when you notice a knowledge gap, want to explore a technology "
                "more deeply, or encounter something you'd like to understand better. "
                "The CuriosityModule will pick it up and research it — including testing "
                "things in the sandbox if applicable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Short name for the topic (e.g. 'WebRTC signalling', 'CRDT data structures').",
                    },
                    "description": {
                        "type": "string",
                        "description": "What you want to learn. More context = better research output.",
                    },
                    "priority": {
                        "type": "integer",
                        "description": "1 (urgent) to 10 (low). Default 5.",
                    },
                },
                "required": ["topic"],
            },
        },
        "func": queue_research,
    }]
