"""Tests for AdapterManager._get_conv_lock bounded-dict eviction.

Since AdapterManager has heavy transitive imports (discord, sentence_transformers,
torch, neo4j) that are not available in CI, we test the bounded-dict eviction
pattern in isolation.  The actual ``_get_conv_lock`` method is simple enough
that the pattern *is* the implementation — the only risk is a typo in
``MAX_CONV_LOCKS`` or the eviction line.
"""
import asyncio


class TestConvLockEviction:
    """Validate the bounded-dict pattern used in _get_conv_lock."""

    MAX_CONV_LOCKS = 500

    @staticmethod
    def _get_conv_lock(conv_locks: dict, conv_id: str) -> asyncio.Lock:
        """Exact copy of AdapterManager._get_conv_lock logic."""
        if conv_id not in conv_locks:
            if len(conv_locks) >= TestConvLockEviction.MAX_CONV_LOCKS:
                for key, lock in list(conv_locks.items()):
                    if not lock.locked():
                        del conv_locks[key]
                        break
            conv_locks[conv_id] = asyncio.Lock()
        return conv_locks[conv_id]

    def test_creates_lock_on_first_access(self):
        locks: dict[str, asyncio.Lock] = {}
        lock = self._get_conv_lock(locks, "channel-1")
        assert isinstance(lock, asyncio.Lock)
        assert "channel-1" in locks

    def test_returns_same_lock_for_same_id(self):
        locks: dict[str, asyncio.Lock] = {}
        lock_a = self._get_conv_lock(locks, "channel-1")
        lock_b = self._get_conv_lock(locks, "channel-1")
        assert lock_a is lock_b

    def test_does_not_exceed_max_locks(self):
        locks: dict[str, asyncio.Lock] = {}
        limit = self.MAX_CONV_LOCKS

        for i in range(limit):
            self._get_conv_lock(locks, f"channel-{i}")

        assert len(locks) == limit
        assert "channel-0" in locks

        # One more should evict the oldest
        self._get_conv_lock(locks, "channel-overflow")

        assert len(locks) == limit
        assert "channel-0" not in locks
        assert "channel-overflow" in locks

    def test_fifo_eviction_order(self):
        """Oldest-created *idle* lock is evicted first (FIFO).

        Python dicts preserve *insertion* order (not access order), so
        eviction walks insertion order and picks the first unlocked lock.
        """
        locks: dict[str, asyncio.Lock] = {}
        limit = self.MAX_CONV_LOCKS

        # Fill to limit
        for i in range(limit):
            self._get_conv_lock(locks, f"channel-{i}")

        # Overfill by 3 — oldest 3 should be evicted
        for i in range(3):
            self._get_conv_lock(locks, f"extra-{i}")

        assert len(locks) == limit
        assert "channel-0" not in locks  # oldest
        assert "channel-1" not in locks
        assert "channel-2" not in locks
        assert "channel-3" in locks  # survived
        assert "extra-0" in locks

    def test_does_not_evict_held_lock(self):
        """Held locks are skipped — the first *idle* entry is evicted."""
        locks: dict[str, asyncio.Lock] = {}
        limit = self.MAX_CONV_LOCKS

        for i in range(limit):
            self._get_conv_lock(locks, f"channel-{i}")

        async def _hold_and_overflow():
            # Acquire the oldest lock and keep it held
            async with locks["channel-0"]:
                assert locks["channel-0"].locked()
                # Overflow while channel-0 is held:
                # channel-1 should be evicted instead
                self._get_conv_lock(locks, "channel-overflow")

        asyncio.run(_hold_and_overflow())

        assert len(locks) == limit
        assert "channel-0" in locks  # held, not evicted
        assert "channel-1" not in locks  # first idle, evicted
        assert "channel-overflow" in locks

    def test_stress_many_ids_stays_bounded(self):
        locks: dict[str, asyncio.Lock] = {}
        limit = self.MAX_CONV_LOCKS

        for i in range(limit * 5):
            self._get_conv_lock(locks, f"channel-{i}")
            assert len(locks) <= limit

        assert len(locks) == limit
        # First 4×limit entries should be evicted
        for i in range(limit * 4):
            assert f"channel-{i}" not in locks
        # Last `limit` entries should be present
        for i in range(limit * 4, limit * 5):
            assert f"channel-{i}" in locks
