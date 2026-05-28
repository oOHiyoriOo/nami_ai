"""
Tests for lib/services/heartbeat_modules/dream.py — DreamModule

Covers:
- record_activity() — updates _last_message_at timestamp
- condition() — 4 gates: enabled, idle, new memories, no dream running
- action() — spawns background asyncio.Task
- _init_db() — creates dream_state table, restores state
- _get_state() / _set_state() — SQLite read/write float values
- _count_new_memories() — Neo4j query across MEMORY_TYPES
- _run_dream() — config abort, exception handling
"""

import asyncio
import importlib.util
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

_PROJECT = Path(__file__).parent.parent

# Pre-load heartbeat_module base class into sys.modules to avoid
# triggering lib.services.__init__ (which has heavy transitive imports).
_hbm_path = _PROJECT / "lib" / "services" / "heartbeat_module.py"
_hbm_spec = importlib.util.spec_from_file_location(
    "lib.services.heartbeat_module", _hbm_path,
)
_hbm = importlib.util.module_from_spec(_hbm_spec)
sys.modules["lib.services.heartbeat_module"] = _hbm
_hbm_spec.loader.exec_module(_hbm)

# Pre-register stub modules for imports triggered inside _run_dream().
# The heavy lib.services.__init__ chain pulls in app_initializer → colorama etc.
_saved_sys_modules = {}
for _key in ["lib.services", "lib.services.tool_executor", "lib.services.tool_context", "lib.ai_providers"]:
    _saved_sys_modules[_key] = sys.modules.get(_key)

_svc_stub = MagicMock()
sys.modules["lib.services"] = _svc_stub
sys.modules["lib.services.tool_executor"] = MagicMock()
sys.modules["lib.services.tool_context"] = MagicMock()
sys.modules["lib.ai_providers"] = MagicMock()

try:
    # Now load DreamModule — when dream.py does `from lib.services.heartbeat_module
    # import HeartbeatModule`, Python finds it in sys.modules.
    _DREAM_PATH = _PROJECT / "lib" / "services" / "heartbeat_modules" / "dream.py"
    _spec = importlib.util.spec_from_file_location(
        "lib.services.heartbeat_modules.dream", _DREAM_PATH,
    )
    _dream = importlib.util.module_from_spec(_spec)
    sys.modules["lib.services.heartbeat_modules.dream"] = _dream
    _spec.loader.exec_module(_dream)
    DreamModule = _dream.DreamModule
finally:
    for _key, _saved in _saved_sys_modules.items():
        if _saved is not None:
            sys.modules[_key] = _saved
        else:
            sys.modules.pop(_key, None)
    sys.modules.pop("lib.services.heartbeat_modules.dream", None)

from lib.configuration_file import ConfigurationFile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _make_cfg(**dream_overrides):
    dream = {"enabled": True, "min_idle_hours": 2.0, "min_new_memories": 5}
    dream.update(dream_overrides)
    memory = {"extraction_provider": "ollama", "extraction_model": "llama3.2"}
    return ConfigurationFile("test", {
        "dream": dream,
        "memory": memory,
        "providers": {"ollama": {"base_url": "http://localhost:11434"}},
        "default_model": "llama3.2",
    })


def _make_dm(db_path=None, **dream_overrides):
    return DreamModule(config=_make_cfg(**dream_overrides), db_path=db_path or _tmp_db())


def _mock_mem_db(new_counts):
    """Create mock memory_db with async session returning per-type counts."""
    mock_db = MagicMock()
    mock_driver = MagicMock()
    mock_session = MagicMock()

    mock_driver.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_driver.session.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_db.MEMORY_TYPES = ["EpisodicMemory", "KnowledgeUnit"]

    call = 0

    async def fake_run(_query, _params):
        nonlocal call
        res = MagicMock()
        res.single = AsyncMock(return_value={"n": new_counts[call]})
        call += 1
        return res

    mock_session.run = fake_run
    mock_db.get_driver = MagicMock(return_value=mock_driver)
    return mock_db


# ---------------------------------------------------------------------------
# record_activity()
# ---------------------------------------------------------------------------

def test_record_activity_updates_timestamp():
    """record_activity() updates _last_message_at to current time."""
    db_path = _tmp_db()
    try:
        dm = _make_dm(db_path)
        with patch("time.time", return_value=1700000000.0):
            dm.record_activity()
        assert dm._last_message_at == 1700000000.0
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# condition() gate tests
# ---------------------------------------------------------------------------

def test_condition_returns_false_when_disabled():
    """condition() returns False when dream.enabled is False."""
    db_path = _tmp_db()
    try:
        dm = _make_dm(db_path, enabled=False)

        async def run():
            await dm._init_db()
            result = await dm.condition()
            assert result is False

        asyncio.run(run())
    finally:
        os.unlink(db_path)


def test_condition_returns_false_when_idle_too_short():
    """condition() returns False when idle_hours < min_idle_hours."""
    db_path = _tmp_db()
    try:
        dm = _make_dm(db_path, min_idle_hours=10.0)

        async def run():
            await dm._init_db()
            dm._last_message_at = time.time()  # just now, so idle ≈ 0
            result = await dm.condition()
            assert result is False

        asyncio.run(run())
    finally:
        os.unlink(db_path)


def test_condition_returns_false_when_not_enough_new_memories():
    """condition() returns False when new memories < min_new_memories."""
    db_path = _tmp_db()
    try:
        dm = _make_dm(db_path, min_idle_hours=0.0, min_new_memories=10)

        async def run():
            await dm._init_db()
            dm._last_message_at = 0.0  # huge idle
            mock_db = _mock_mem_db([3, 2])  # total 5 < 10
            with patch.object(_dream, "g_data") as mock_g:
                mock_g.get.return_value = mock_db
                result = await dm.condition()
            assert result is False

        asyncio.run(run())
    finally:
        os.unlink(db_path)


def test_condition_returns_false_when_memory_db_unavailable():
    """condition() returns False when g_data.get('memory_db') is None."""
    db_path = _tmp_db()
    try:
        dm = _make_dm(db_path, min_idle_hours=0.0)

        async def run():
            await dm._init_db()
            dm._last_message_at = 0.0  # huge idle
            with patch.object(_dream, "g_data") as mock_g:
                mock_g.get.return_value = None
                result = await dm.condition()
            assert result is False

        asyncio.run(run())
    finally:
        os.unlink(db_path)


def test_condition_returns_false_when_dream_already_running():
    """condition() returns False when a dream is already active."""
    db_path = _tmp_db()
    try:
        dm = _make_dm(db_path, min_idle_hours=0.0, min_new_memories=0)

        async def run():
            await dm._init_db()
            dm._last_message_at = 0.0
            mock_db = _mock_mem_db([10, 10])
            dm._active_dream = MagicMock()
            dm._active_dream.done.return_value = False
            with patch.object(_dream, "g_data") as mock_g:
                mock_g.get.return_value = mock_db
                result = await dm.condition()
            assert result is False

        asyncio.run(run())
    finally:
        os.unlink(db_path)


def test_condition_returns_true_when_all_gates_pass():
    """condition() returns True when all four gates pass."""
    db_path = _tmp_db()
    try:
        dm = _make_dm(db_path, min_idle_hours=0.0, min_new_memories=5)

        async def run():
            await dm._init_db()
            dm._last_message_at = 0.0  # huge idle
            dm._active_dream = None
            mock_db = _mock_mem_db([5, 5])  # total 10 >= 5
            # Patch _is_nighttime so time-of-day gate passes regardless of clock
            with patch.object(dm, "_is_nighttime", return_value=True):
                with patch.object(_dream, "g_data") as mock_g:
                    # Return None for curiosity_module (no research in progress)
                    # and mock_db for memory_db queries
                    mock_g.get.side_effect = lambda key: (
                        None if key == "curiosity_module" else mock_db
                    )
                    result = await dm.condition()
            assert result is True

        asyncio.run(run())
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# action()
# ---------------------------------------------------------------------------

def test_action_spawns_background_task():
    """action() sets _active_dream as an asyncio.Task."""
    db_path = _tmp_db()
    try:
        dm = _make_dm(db_path)

        async def run():
            dm._run_dream = AsyncMock()  # prevent actual execution
            await dm.action()
            assert dm._active_dream is not None
            assert isinstance(dm._active_dream, asyncio.Task)
            assert dm._active_dream.get_name() == "dream_agent"
            # Wait for the background task to complete
            await dm._active_dream

        asyncio.run(run())
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# _init_db()
# ---------------------------------------------------------------------------

def test_init_db_creates_table():
    """_init_db() creates the dream_state table."""
    import aiosqlite
    db_path = _tmp_db()
    try:
        dm = _make_dm(db_path)

        async def run():
            await dm._init_db()
            async with aiosqlite.connect(db_path) as db:
                async with db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='dream_state'"
                ) as cur:
                    row = await cur.fetchone()
            assert row is not None
            assert row[0] == "dream_state"

        asyncio.run(run())
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# _get_state() / _set_state() — now delegated to SqliteKVStore
# ---------------------------------------------------------------------------

def test_get_state_returns_default_when_no_row():
    """SqliteKVStore.get() returns default=0.0 when key doesn't exist."""
    db_path = _tmp_db()
    try:
        dm = _make_dm(db_path)

        async def run():
            await dm._init_db()
            val = await dm._state.get("nonexistent", default=99.9)
            assert val == 99.9

        asyncio.run(run())
    finally:
        os.unlink(db_path)


def test_set_state_and_get_state_roundtrip():
    """SqliteKVStore.set() persists a float that get() can read back."""
    db_path = _tmp_db()
    try:
        dm = _make_dm(db_path)

        async def run():
            await dm._init_db()
            await dm._state.set("test_key", 42.5)
            val = await dm._state.get("test_key")
            assert val == 42.5

        asyncio.run(run())
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# _count_new_memories()
# ---------------------------------------------------------------------------

def test_count_new_memories_aggregates_across_labels():
    """_count_new_memories() sums counts across all MEMORY_TYPES."""
    db_path = _tmp_db()
    try:
        dm = _make_dm(db_path)
        mock_db = _mock_mem_db([7, 3])  # 7 + 3 = 10

        async def run():
            count = await dm._count_new_memories(mock_db, 1000.0)
            assert count == 10

        asyncio.run(run())
    finally:
        os.unlink(db_path)


def test_count_new_memories_handles_neo4j_error():
    """_count_new_memories() returns 0 when Neo4j raises."""
    db_path = _tmp_db()
    try:
        dm = _make_dm(db_path)
        mock_db = MagicMock()
        mock_db.MEMORY_TYPES = ["EpisodicMemory"]
        mock_driver = MagicMock()
        mock_driver.session.side_effect = RuntimeError("Neo4j unavailable")
        mock_db.get_driver = MagicMock(return_value=mock_driver)

        async def run():
            count = await dm._count_new_memories(mock_db, 1000.0)
            assert count == 0

        asyncio.run(run())
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# _run_dream()
# ---------------------------------------------------------------------------

def test_run_dream_aborts_when_no_config():
    """_run_dream() returns early when g_data.get('cfg') is None."""
    db_path = _tmp_db()
    try:
        dm = _make_dm(db_path)

        async def run():
            with patch.object(_dream, "g_data") as mock_g:
                mock_g.get.return_value = None
                # Should not raise; just log and return
                await dm._run_dream()

        asyncio.run(run())
    finally:
        os.unlink(db_path)


def test_run_dream_catches_exceptions():
    """_run_dream() does not crash when provider or tool loop raises."""
    db_path = _tmp_db()
    try:
        dm = _make_dm(db_path)

        async def run():
            mock_cfg = MagicMock()
            mock_cfg.data = {
                "providers": {"ollama": {"base_url": "http://localhost:11434"}},
                "default_model": "llama3.2",
            }
            mock_provider = AsyncMock()
            mock_provider.chat.side_effect = RuntimeError("Provider crash")

            with patch.object(_dream, "g_data") as mock_g:
                mock_g.get.return_value = mock_cfg
                with patch.object(
                    sys.modules["lib.ai_providers"], "ProviderRegistry"
                ) as mock_registry:
                    mock_registry.get_provider.return_value = mock_provider
                    # Should not raise; logs error and returns
                    await dm._run_dream()

        asyncio.run(run())
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
