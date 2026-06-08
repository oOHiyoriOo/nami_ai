"""Lightweight async database abstraction for pluggable backends.

Currently only the SQLite backend (via aiosqlite) is implemented.
A future PostgreSQL backend can be added by implementing the same
interface against ``asyncpg``.

Services receive a ``Database`` instance instead of managing
``aiosqlite.Connection`` directly, making it possible to switch
backends without touching service code.

Usage::

    db = Database(backend="sqlite", path="app.db")
    await db.execute("CREATE TABLE ...")
    rows = await db.fetch_all("SELECT * FROM ...")
    await db.close()

    # Or use as a context manager:
    async with Database(backend="sqlite", path="app.db") as db:
        row = await db.fetch_one("SELECT ... WHERE id = ?", 42)
"""

from __future__ import annotations

import aiosqlite


class Database:
    """Async database handle with a pluggable backend.

    Args:
        backend: Database backend name — currently only ``"sqlite"``.
        path:    Path to the database file (SQLite) or DSN (future).
    """

    def __init__(self, backend: str = "sqlite", path: str | None = None) -> None:
        if backend != "sqlite":
            raise ValueError(f"Unsupported backend: {backend!r}")
        self._backend = backend
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> Database:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    async def execute(self, sql: str, *params: object) -> aiosqlite.Cursor:
        """Execute a statement and return the cursor.

        Use for INSERT / UPDATE / DELETE.  Call :meth:`commit` afterwards
        to persist changes.  The returned cursor exposes ``rowcount``.

        Example::

            cur = await db.execute("DELETE FROM t WHERE id = ?", 42)
            await db.commit()
            print(cur.rowcount)  # rows affected
        """
        db = await self._ensure_conn()
        return await db.execute(sql, params)

    async def fetch_all(self, sql: str, *params: object) -> list[aiosqlite.Row]:
        """Execute a query and return all rows as :class:`aiosqlite.Row` objects.

        Each row supports dict-style access (``row["column"]``).
        """
        db = await self._ensure_conn()
        db.row_factory = aiosqlite.Row
        try:
            async with db.execute(sql, params) as cursor:
                return await cursor.fetchall()
        finally:
            db.row_factory = None

    async def fetch_one(self, sql: str, *params: object) -> aiosqlite.Row | None:
        """Execute a query and return the first row, or *None*."""
        db = await self._ensure_conn()
        db.row_factory = aiosqlite.Row
        try:
            async with db.execute(sql, params) as cursor:
                return await cursor.fetchone()
        finally:
            db.row_factory = None

    async def fetch_val(self, sql: str, *params: object) -> object:
        """Execute a query and return the first column of the first row, or *None*."""
        db = await self._ensure_conn()
        async with db.execute(sql, params) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else None

    async def commit(self) -> None:
        """Persist any pending writes."""
        if self._conn:
            await self._conn.commit()

    async def close(self) -> None:
        """Close the underlying connection if open."""
        if self._conn:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _ensure_conn(self) -> aiosqlite.Connection:
        """Return the persistent connection, opening it lazily."""
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._path)
        return self._conn
