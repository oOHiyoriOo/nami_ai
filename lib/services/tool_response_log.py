"""
tool_response_log.py — SQLite-backed tool response storage.

Replaces bulky tool responses (results, errors, file reads, web pages) with
UUID placeholders in chat history after the current turn.  The AI sees full
responses during the turn; afterwards they're stored here and the history
gets ``[TOOL_RESPONSE:uuid]`` markers.

Usage::

    log = ToolResponseLog("tool_responses.db")
    await log.initialize()
    uuid = await log.store("sandbox_read_file", "file contents...")
    record = await log.get(uuid)  # {"tool_name": ..., "response_text": ...}
    await log.prune_old(retention_days=30)
"""

import uuid
import json
import logging
from datetime import datetime, timezone

import aiosqlite

PLACEHOLDER_PREFIX = "[TOOL_RESPONSE:"
PLACEHOLDER_SUFFIX = "]"


def make_placeholder(response_uuid: str) -> str:
    """Return a ``[TOOL_RESPONSE:<uuid>]`` placeholder string."""
    return f"{PLACEHOLDER_PREFIX}{response_uuid}{PLACEHOLDER_SUFFIX}"


def parse_placeholder(text: str) -> str | None:
    """Extract UUID from a ``[TOOL_RESPONSE:uuid]`` string, or None."""
    if text.startswith(PLACEHOLDER_PREFIX) and text.endswith(PLACEHOLDER_SUFFIX):
        return text[len(PLACEHOLDER_PREFIX):-len(PLACEHOLDER_SUFFIX)]
    return None


class ToolResponseLog:
    """Stores tool responses in a flat SQLite table keyed by UUID."""

    _TABLE = "tool_response_log"

    def __init__(self, db_path: str = "tool_responses.db"):
        self.db_path = db_path

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create the table and indices if they don't exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._TABLE} (
                    uuid            TEXT PRIMARY KEY,
                    timestamp       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    tool_name       TEXT    NOT NULL,
                    response_text   TEXT    NOT NULL,
                    metadata        TEXT    NOT NULL DEFAULT '{{}}'
                )
            """)
            await db.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_toolresp_timestamp
                    ON {self._TABLE} (timestamp)
            """)
            await db.commit()
        logging.info("ToolResponseLog initialized (db=%s)", self.db_path)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def store(
        self,
        tool_name: str,
        response_text: str,
        metadata: dict | None = None,
    ) -> str:
        """Store a tool response and return its UUID.

        Args:
            tool_name:    Name of the tool that produced the response.
            response_text: Full response content (may be large).
            metadata:     Optional dict of extra context (tool_call_id, etc.).

        Returns:
            UUID string usable with :func:`make_placeholder`.
        """
        response_uuid = uuid.uuid4().hex
        meta_json = json.dumps(metadata or {})
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"INSERT INTO {self._TABLE} (uuid, tool_name, response_text, metadata) "
                "VALUES (?, ?, ?, ?)",
                (response_uuid, tool_name, response_text, meta_json),
            )
            await db.commit()
        logging.debug(
            "[tool_response_log] stored %s (%d chars)",
            response_uuid, len(response_text),
        )
        return response_uuid

    async def get(self, response_uuid: str) -> dict | None:
        """Retrieve a stored tool response by UUID.

        Returns:
            Dict with keys ``tool_name``, ``response_text``, ``metadata``, ``timestamp``,
            or ``None`` if not found.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT tool_name, response_text, metadata, timestamp "
                f"FROM {self._TABLE} WHERE uuid = ?",
                (response_uuid,),
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None
        return {
            "tool_name": row["tool_name"],
            "response_text": row["response_text"],
            "metadata": json.loads(row["metadata"]),
            "timestamp": row["timestamp"],
        }

    async def delete(self, response_uuid: str) -> bool:
        """Delete a stored response. Returns True if a row was removed."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                f"DELETE FROM {self._TABLE} WHERE uuid = ?",
                (response_uuid,),
            )
            await db.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    async def prune_old(self, retention_days: int = 30) -> int:
        """Remove responses older than *retention_days*. Returns count deleted."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                f"DELETE FROM {self._TABLE} "
                "WHERE timestamp < datetime('now', ? || ' days')",
                (f"-{retention_days}",),
            )
            await db.commit()
        if cursor.rowcount:
            logging.info(
                "[tool_response_log] Pruned %d entries older than %d days",
                cursor.rowcount, retention_days,
            )
        return cursor.rowcount

    async def get_count(self) -> int:
        """Return total number of stored responses."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(f"SELECT COUNT(*) FROM {self._TABLE}") as cursor:
                row = await cursor.fetchone()
        return row[0] if row else 0
