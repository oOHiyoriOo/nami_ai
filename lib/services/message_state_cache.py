"""
message_state_cache.py — SQLite-backed message lifecycle state cache.

Tracks every inbound message from receipt through delivery, enabling:

- Adapter reconnect recovery (adapters query state by ``conversation_id``)
- Re-delivery of missed responses (adapter disconnected at the wrong moment)
- Re-queuing of lost messages after a server crash/restart

States
------
``queued``      Message received, waiting to be picked up by the pipeline.
``processing``  Pipeline is actively running for this message.
``done``        Pipeline finished — response is stored in ``response`` field.
``error``       Pipeline failed — response holds an error hint.

Entries expire TTL hours after reaching a terminal state (``done``/``error``)
and are pruned by a background task started with :meth:`MessageStateCache.start`.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

logger = logging.getLogger(__name__)

_TTL_HOURS = 1

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS message_state (
    conversation_id TEXT PRIMARY KEY,
    state           TEXT    NOT NULL,
    adapter         TEXT    NOT NULL,
    event_json      TEXT    NOT NULL,
    response        TEXT,
    queued_at       TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    expires_at      TEXT    NOT NULL
)
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expiry(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


class MessageStateCache:
    """SQLite-backed cache for message lifecycle state.

    One row per active conversation.  Rows expire ``ttl_hours`` after a
    terminal state is set and are pruned by a background task.

    Args:
        db_path:   Path to the SQLite database file (shared with scheduler).
        ttl_hours: How long to retain completed entries before deletion.
    """

    def __init__(self, db_path: str = "scheduler.db", ttl_hours: float = _TTL_HOURS) -> None:
        self._db_path = db_path
        self._ttl_hours = ttl_hours
        self._cleanup_task: asyncio.Task | None = None

    async def init(self) -> None:
        """Create the ``message_state`` table if it does not already exist."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_CREATE_TABLE)
            await db.commit()
        logger.info("[msg_state_cache] initialised (db=%s)", self._db_path)

    def start(self) -> None:
        """Start the background TTL cleanup task."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self) -> None:
        """Cancel the background cleanup task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def put(self, conversation_id: str, adapter: str, event: dict) -> None:
        """Insert or replace a cache entry in ``queued`` state.

        Args:
            conversation_id: Unique conversation identifier.
            adapter:         Name of the originating adapter (e.g. ``"discord"``).
            event:           Original ``message.received`` payload — serialised
                             so it can be re-published if the queue is lost.
        """
        now = _now()
        # Queued entries get a longer TTL so they survive until processed
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO message_state
                    (conversation_id, state, adapter, event_json,
                     response, queued_at, updated_at, expires_at)
                VALUES (?, 'queued', ?, ?, NULL, ?, ?, ?)
                """,
                (
                    conversation_id,
                    adapter,
                    json.dumps(event),
                    now,
                    now,
                    _expiry(self._ttl_hours * 24),  # keep queued entries 24× longer
                ),
            )
            await db.commit()

    async def set_processing(self, conversation_id: str) -> None:
        """Transition a queued entry to ``processing`` state.

        Args:
            conversation_id: Conversation to update.
        """
        await self._update_state(conversation_id, "processing")

    async def set_done(self, conversation_id: str, response: str) -> None:
        """Mark an entry as complete with the AI's response content.

        Args:
            conversation_id: Conversation to update.
            response:        AI response text (may be ``"<ignore>"``).
        """
        now = _now()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                UPDATE message_state
                SET state='done', response=?, updated_at=?, expires_at=?
                WHERE conversation_id=?
                """,
                (response, now, _expiry(self._ttl_hours), conversation_id),
            )
            await db.commit()

    async def set_error(self, conversation_id: str) -> None:
        """Mark an entry as errored.

        Args:
            conversation_id: Conversation to update.
        """
        await self._update_state(
            conversation_id, "error", expiry=_expiry(self._ttl_hours)
        )

    async def get(self, conversation_id: str) -> dict | None:
        """Retrieve a cache entry by conversation ID.

        Args:
            conversation_id: Conversation to look up.

        Returns:
            Dict with keys ``state``, ``adapter``, ``event``, ``response``,
            ``queued_at``, ``updated_at`` — or ``None`` if not found.
        """
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM message_state WHERE conversation_id=?",
                (conversation_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None
        return {
            "state": row["state"],
            "adapter": row["adapter"],
            "event": json.loads(row["event_json"]),
            "response": row["response"],
            "queued_at": row["queued_at"],
            "updated_at": row["updated_at"],
        }

    # ------------------------------------------------------------------
    # Startup recovery
    # ------------------------------------------------------------------

    async def requeue_lost(self, event_bus) -> int:
        """Re-publish messages stuck in ``queued``/``processing`` at startup.

        Called once during server startup to recover from a crash or restart.
        Each recovered entry is re-published as a ``message.received`` event
        so the pipeline handler picks it up normally.

        Args:
            event_bus: Application :class:`EventBus` instance.

        Returns:
            Number of entries re-queued.
        """
        from lib.services.event_bus import Event as _Event

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM message_state WHERE state IN ('queued', 'processing')"
            ) as cursor:
                rows = await cursor.fetchall()

        count = 0
        for row in rows:
            try:
                event_data = json.loads(row["event_json"])
                await event_bus.publish(_Event(type="message.received", data=event_data))
                logger.info(
                    "[msg_state_cache] re-queued lost message conv=%s (was %s)",
                    row["conversation_id"],
                    row["state"],
                )
                count += 1
            except Exception:
                logger.error(
                    "[msg_state_cache] failed to re-queue conv=%s",
                    row["conversation_id"],
                    exc_info=True,
                )

        if count:
            logger.info(
                "[msg_state_cache] recovered %d lost message(s) after restart", count
            )
        return count

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _update_state(
        self, conversation_id: str, state: str, expiry: str | None = None
    ) -> None:
        now = _now()
        async with aiosqlite.connect(self._db_path) as db:
            if expiry:
                await db.execute(
                    """
                    UPDATE message_state
                    SET state=?, updated_at=?, expires_at=?
                    WHERE conversation_id=?
                    """,
                    (state, now, expiry, conversation_id),
                )
            else:
                await db.execute(
                    "UPDATE message_state SET state=?, updated_at=? WHERE conversation_id=?",
                    (state, now, conversation_id),
                )
            await db.commit()

    async def _cleanup_loop(self) -> None:
        """Background task: delete entries past their ``expires_at`` every 10 min."""
        while True:
            try:
                await asyncio.sleep(600)
                async with aiosqlite.connect(self._db_path) as db:
                    cursor = await db.execute(
                        "DELETE FROM message_state WHERE expires_at < ?", (_now(),)
                    )
                    await db.commit()
                    if cursor.rowcount:
                        logger.debug(
                            "[msg_state_cache] pruned %d expired entries",
                            cursor.rowcount,
                        )
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("[msg_state_cache] cleanup error", exc_info=True)
