"""
Tests for DreamService — autonomous dream feature.

Covers:
- record_activity() — updates last_message_at timestamp
- _get_state() / _set_state() — SQLite-backed float persistence
- _maybe_dream() — gate conditions (idle, new memories, enabled)
- start() / stop() — proper lifecycle with task cancellation
"""

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import importlib.util
from lib.configuration_file import ConfigurationFile

_saved_dream_svc = sys.modules.get("dream_service")
try:
    _dream_service_path = Path(__file__).parent.parent / "lib" / "services" / "dream_service.py"
    _spec = importlib.util.spec_from_file_location("dream_service", _dream_service_path)
    _dream_module = importlib.util.module_from_spec(_spec)
    sys.modules["dream_service"] = _dream_module
    _spec.loader.exec_module(_dream_module)
    DreamService = _dream_module.DreamService
finally:
    if _saved_dream_svc is None:
        sys.modules.pop("dream_service", None)
    else:
        sys.modules["dream_service"] = _saved_dream_svc


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _make_cfg(**dream_overrides):
    dream = {"enabled": True, "min_idle_hours": 2.0, "min_new_memories": 5}
    dream.update(dream_overrides)
    memory = {"extraction_provider": "ollama", "extraction_model": "llama3.2"}
    return ConfigurationFile("test", {"dream": dream, "memory": memory})


def _mock_mem_db(new_count):
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
        if call == 0:
            res.single = AsyncMock(return_value={"n": new_count // 2 + new_count % 2})
        else:
            res.single = AsyncMock(return_value={"n": new_count // 2})
        call += 1
        return res

    mock_session.run = fake_run
    mock_db.get_driver = MagicMock(return_value=mock_driver)
    return mock_db


# ─────────────────────────────────────────────────────────────────────
# record_activity()
# ─────────────────────────────────────────────────────────────────────

def test_record_activity():
    db_path = _tmp_db()
    try:
        ds = DreamService(config=_make_cfg(), db_path=db_path)
        with patch("time.time", return_value=1700000000.0):
            ds.record_activity()
        assert ds._last_message_at == 1700000000.0
    finally:
        os.unlink(db_path)


# ─────────────────────────────────────────────────────────────────────
# _get_state() / _set_state()
# ─────────────────────────────────────────────────────────────────────

def test_get_set_state():
    db_path = _tmp_db()
    try:
        ds = DreamService(config=_make_cfg(), db_path=db_path)

        async def run():
            await ds._init_db()
            await ds._set_state("test_key", 42.5)
            assert await ds._get_state("test_key") == 42.5
            assert await ds._get_state("nonexistent", default=99.0) == 99.0

        asyncio.run(run())
    finally:
        os.unlink(db_path)


def test_get_state_default_zero():
    db_path = _tmp_db()
    try:
        ds = DreamService(config=_make_cfg(), db_path=db_path)

        async def run():
            await ds._init_db()
            assert await ds._get_state("nonexistent") == 0.0

        asyncio.run(run())
    finally:
        os.unlink(db_path)


def test_set_state_overwrites():
    db_path = _tmp_db()
    try:
        ds = DreamService(config=_make_cfg(), db_path=db_path)

        async def run():
            await ds._init_db()
            await ds._set_state("key", 1.0)
            await ds._set_state("key", 999.0)
            assert await ds._get_state("key") == 999.0

        asyncio.run(run())
    finally:
        os.unlink(db_path)


# ─────────────────────────────────────────────────────────────────────
# _maybe_dream() gate conditions
# ─────────────────────────────────────────────────────────────────────

def test_maybe_dream_skips_when_disabled():
    db_path = _tmp_db()
    try:
        ds = DreamService(config=_make_cfg(enabled=False), db_path=db_path)

        async def run():
            await ds._init_db()
            with patch.object(ds, "_run_dream") as mock_run:
                await ds._maybe_dream()
                mock_run.assert_not_called()

        asyncio.run(run())
    finally:
        os.unlink(db_path)


def test_maybe_dream_skips_when_not_idle():
    db_path = _tmp_db()
    try:
        ds = DreamService(config=_make_cfg(min_idle_hours=10.0), db_path=db_path)

        async def run():
            await ds._init_db()
            ds._last_message_at = time.time()
            with patch.object(ds, "_run_dream") as mock_run:
                await ds._maybe_dream()
                mock_run.assert_not_called()

        asyncio.run(run())
    finally:
        os.unlink(db_path)


def test_maybe_dream_skips_when_not_enough_memories():
    db_path = _tmp_db()
    try:
        ds = DreamService(
            config=_make_cfg(min_idle_hours=0.0, min_new_memories=10),
            db_path=db_path,
        )
        mock_db = _mock_mem_db(new_count=3)

        async def run():
            await ds._init_db()
            ds._last_message_at = 0.0
            with patch("lib.global_registry.g_data") as mock_g:
                mock_g.get.return_value = mock_db
                with patch.object(ds, "_run_dream") as mock_run:
                    await ds._maybe_dream()
                    mock_run.assert_not_called()

        asyncio.run(run())
    finally:
        os.unlink(db_path)


def test_maybe_dream_skips_when_memory_db_missing():
    db_path = _tmp_db()
    try:
        ds = DreamService(config=_make_cfg(min_idle_hours=0.0), db_path=db_path)

        async def run():
            await ds._init_db()
            ds._last_message_at = 0.0
            with patch("lib.global_registry.g_data") as mock_g:
                mock_g.get.return_value = None
                with patch.object(ds, "_run_dream") as mock_run:
                    await ds._maybe_dream()
                    mock_run.assert_not_called()

        asyncio.run(run())
    finally:
        os.unlink(db_path)


def test_maybe_dream_skips_when_dream_already_running():
    db_path = _tmp_db()
    try:
        ds = DreamService(
            config=_make_cfg(min_idle_hours=0.0, min_new_memories=0),
            db_path=db_path,
        )
        mock_db = _mock_mem_db(new_count=10)

        async def run():
            await ds._init_db()
            ds._last_message_at = 0.0
            ds._active_dream = MagicMock()
            ds._active_dream.done.return_value = False

            with patch("lib.global_registry.g_data") as mock_g:
                mock_g.get.return_value = mock_db
                with patch.object(ds, "_run_dream") as mock_run:
                    await ds._maybe_dream()
                    mock_run.assert_not_called()

        asyncio.run(run())
    finally:
        os.unlink(db_path)


def test_maybe_dream_triggers_when_conditions_met():
    db_path = _tmp_db()
    try:
        ds = DreamService(
            config=_make_cfg(min_idle_hours=0.0, min_new_memories=5),
            db_path=db_path,
        )
        mock_db = _mock_mem_db(new_count=10)

        async def run():
            await ds._init_db()
            ds._last_message_at = 0.0
            with patch("lib.global_registry.g_data") as mock_g:
                mock_g.get.return_value = mock_db
                with patch.object(ds, "_run_dream", new_callable=AsyncMock) as mock_run:
                    await ds._maybe_dream()
                    mock_run.assert_called_once()

        asyncio.run(run())
    finally:
        os.unlink(db_path)


def test_maybe_dream_sets_last_dream_at():
    db_path = _tmp_db()
    try:
        ds = DreamService(
            config=_make_cfg(min_idle_hours=0.0, min_new_memories=5),
            db_path=db_path,
        )
        mock_db = _mock_mem_db(new_count=10)

        async def run():
            await ds._init_db()
            ds._last_message_at = 0.0
            with patch("lib.global_registry.g_data") as mock_g:
                mock_g.get.return_value = mock_db
                with patch.object(ds, "_run_dream", new_callable=AsyncMock):
                    with patch("time.time", return_value=1700000000.0):
                        await ds._maybe_dream()
            assert await ds._get_state("last_dream_at") == 1700000000.0

        asyncio.run(run())
    finally:
        os.unlink(db_path)


# ─────────────────────────────────────────────────────────────────────
# start() / stop() lifecycle
# ─────────────────────────────────────────────────────────────────────

def test_start_creates_background_task():
    db_path = _tmp_db()
    try:
        ds = DreamService(config=_make_cfg(), db_path=db_path)

        async def run():
            await ds.start()
            assert ds._task is not None
            assert isinstance(ds._task, asyncio.Task)
            assert not ds._task.done()

        asyncio.run(run())
    finally:
        os.unlink(db_path)


def test_stop_cancels_task():
    db_path = _tmp_db()
    try:
        ds = DreamService(config=_make_cfg(), db_path=db_path)

        async def run():
            await ds.start()
            await ds.stop()
            assert ds._task.done()

        asyncio.run(run())
    finally:
        os.unlink(db_path)


def test_stop_when_no_task():
    db_path = _tmp_db()
    try:
        ds = DreamService(config=_make_cfg(), db_path=db_path)

        async def run():
            await ds.stop()

        asyncio.run(run())
    finally:
        os.unlink(db_path)


def test_start_stop_multiple():
    db_path = _tmp_db()
    try:
        ds = DreamService(config=_make_cfg(), db_path=db_path)

        async def run():
            for _ in range(3):
                await ds.start()
                assert not ds._task.done()
                await ds.stop()
                assert ds._task.done()

        asyncio.run(run())
    finally:
        os.unlink(db_path)


# ─────────────────────────────────────────────────────────────────────
# _init_db() restores state
# ─────────────────────────────────────────────────────────────────────

def test_init_db_restores_last_message_at():
    cfg = _make_cfg()
    db_path = _tmp_db()
    try:
        async def run():
            ds1 = DreamService(config=cfg, db_path=db_path)
            await ds1._init_db()
            await ds1._set_state("last_message_at", 1600000000.0)

            ds2 = DreamService(config=cfg, db_path=db_path)
            await ds2._init_db()
            assert ds2._last_message_at == 1600000000.0

        asyncio.run(run())
    finally:
        os.unlink(db_path)


# ─────────────────────────────────────────────────────────────────────
# _count_new_memories()
# ─────────────────────────────────────────────────────────────────────

def test_count_new_memories_returns_total():
    db_path = _tmp_db()
    try:
        ds = DreamService(config=_make_cfg(), db_path=db_path)

        async def run():
            count = await ds._count_new_memories(_mock_mem_db(15), 1000.0)
            assert count == 15

        asyncio.run(run())
    finally:
        os.unlink(db_path)


def test_count_new_memories_handles_neo4j_error():
    db_path = _tmp_db()
    try:
        ds = DreamService(config=_make_cfg(), db_path=db_path)

        mock_db = MagicMock()
        mock_db.MEMORY_TYPES = ["EpisodicMemory"]
        mock_driver = MagicMock()
        mock_driver.session.side_effect = RuntimeError("Neo4j unavailable")
        mock_db.get_driver = MagicMock(return_value=mock_driver)

        async def run():
            count = await ds._count_new_memories(mock_db, 1000.0)
            assert count == 0

        asyncio.run(run())
    finally:
        os.unlink(db_path)


# ─────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))