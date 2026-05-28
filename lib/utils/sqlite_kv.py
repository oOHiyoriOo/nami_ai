"""Shared SQLite key-value store for heartbeat modules."""

import aiosqlite


class SqliteKVStore:
    """Simple async string→float key-value store backed by a single SQLite table.

    The table (named at construction time) must already exist with columns
    ``key TEXT PRIMARY KEY, value TEXT``.  Use within a heartbeat module::

        self._state = SqliteKVStore(self._db_path, "curiosity_state")
    """

    def __init__(self, db_path: str, table: str) -> None:
        self._db_path = db_path
        self._table = table

    async def get(self, key: str, default: float = 0.0) -> float:
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                f"SELECT value FROM {self._table} WHERE key = ?", (key,)
            ) as cur:
                row = await cur.fetchone()
        return float(row[0]) if row else default

    async def set(self, key: str, value: str | float) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                f"INSERT OR REPLACE INTO {self._table} (key, value) VALUES (?, ?)",
                (key, str(value)),
            )
            await db.commit()

    async def increment(self, key: str, delta: int = 1) -> int:
        """Atomically increment a numeric key and return the new value.

        Uses BEGIN IMMEDIATE to prevent read-modify-write races:
        no other connection can read or write until the transaction commits.
        """
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                f"SELECT value FROM {self._table} WHERE key = ?", (key,)
            ) as cur:
                row = await cur.fetchone()
            current = int(float(row[0])) if row else 0
            new_value = current + delta
            await db.execute(
                f"INSERT OR REPLACE INTO {self._table} (key, value) VALUES (?, ?)",
                (key, str(new_value)),
            )
            await db.commit()
        return new_value
