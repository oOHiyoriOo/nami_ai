"""
Tests for lib/services/nami_session_cache.py — persistent session cache.

Covers:
- generate_session_id format
- init_cache_dir → session.json creation
- cache_edit → saves original (.orig) + modified files
- finalize_cache → result/verification update
- read_cached_file / apply_cached_file
- list_sessions / get_session_detail
- reset_cache (all, specific, keep_failed, dry_run)
- cleanup_old_sessions
- register_commit
"""

import json
import time
from pathlib import Path

import pytest

# Import the module without going through lib.services.__init__
import importlib.util

spec = importlib.util.spec_from_file_location(
    "nsc", Path(__file__).parent.parent / "lib" / "services" / "nami_session_cache.py"
)
nsc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nsc)


# ── generate_session_id ───────────────────────────────────────────────

def test_generate_session_id_format():
    sid = nsc.generate_session_id("Refactor memory_service")
    assert sid  # something was generated
    # Should contain timestamp + slug
    assert "_refactor-memory-service" in sid
    # Timestamp pattern: YYYY-MM-DDTHH-MM-SS
    parts = sid.split("_")
    ts = parts[0]
    assert len(ts) == 19  # YYYY-MM-DDTHH-MM-SS
    assert "T" in ts

def test_generate_session_id_special_chars():
    sid = nsc.generate_session_id("Fix bug: #123 in lib/utils!")
    assert "_fix-bug-123-in-libutils" in sid

def test_generate_session_id_empty():
    sid = nsc.generate_session_id("")
    assert "_unnamed" in sid


# ── init_cache_dir ────────────────────────────────────────────────────

def test_init_cache_dir(tmp_path):
    session_id = "2026-05-27T19-30-00_test"
    session_dir = nsc.init_cache_dir(tmp_path, session_id, "abc123", "Test session")

    assert session_dir.exists()
    assert session_dir.name == session_id

    data = nsc.read_session_json(session_dir)
    assert data is not None
    assert data["session_id"] == session_id
    assert data["description"] == "Test session"
    assert data["safe_point"] == "abc123"
    assert data["result"] == "in_progress"
    assert data["changed_files"] == []
    assert data["commits"] == []
    assert data["verification"] is None
    assert data["started_at"] is not None
    assert data["ended_at"] is None


# ── cache_edit ─────────────────────────────────────────────────────────

def test_cache_edit_saves_orig_and_modified(tmp_path):
    session_dir = nsc.init_cache_dir(tmp_path, "test-id", "abc", "test")
    original = "line1\nline2\nline3"
    modified = "line1\nCHANGED\nline3"

    nsc.cache_edit(session_dir, "lib/mod.py", original, modified)

    # Check .orig file exists
    orig_path = session_dir / "lib" / "mod.py.orig"
    assert orig_path.exists()
    assert orig_path.read_text() == original

    # Check modified file exists
    mod_path = session_dir / "lib" / "mod.py"
    assert mod_path.exists()
    assert mod_path.read_text() == modified

    # Check session.json tracks the file
    data = nsc.read_session_json(session_dir)
    assert "lib/mod.py" in data["changed_files"]


def test_cache_edit_dedup_changed_files(tmp_path):
    session_dir = nsc.init_cache_dir(tmp_path, "test-id", "abc", "test")
    nsc.cache_edit(session_dir, "a.py", "orig_a", "mod_a")
    nsc.cache_edit(session_dir, "a.py", "orig_a2", "mod_a2")  # same file
    nsc.cache_edit(session_dir, "b.py", "orig_b", "mod_b")

    data = nsc.read_session_json(session_dir)
    assert data["changed_files"] == ["a.py", "b.py"]


# ── finalize_cache ─────────────────────────────────────────────────────

def test_finalize_cache_passed(tmp_path):
    session_dir = nsc.init_cache_dir(tmp_path, "test-id", "abc", "test")
    verif = {
        "smoke_test_passed": True,
        "pytest_passed": True,
        "pytest_output": "42 passed in 5s",
        "failed_tests": [],
    }
    nsc.finalize_cache(session_dir, "passed", verif)

    data = nsc.read_session_json(session_dir)
    assert data["result"] == "passed"
    assert data["ended_at"] is not None
    assert data["verification"] == verif


def test_finalize_cache_rolled_back(tmp_path):
    session_dir = nsc.init_cache_dir(tmp_path, "test-id", "abc", "test")
    verif = {
        "smoke_test_passed": False,
        "pytest_passed": False,
        "pytest_output": "3 passed, 2 failed",
        "failed_tests": ["test_x.py::test_broke - KeyError"],
    }
    nsc.finalize_cache(session_dir, "rolled_back", verif)

    data = nsc.read_session_json(session_dir)
    assert data["result"] == "rolled_back"
    assert data["verification"]["failed_tests"] == ["test_x.py::test_broke - KeyError"]


def test_finalize_cache_aborted(tmp_path):
    session_dir = nsc.init_cache_dir(tmp_path, "test-id", "abc", "test")
    nsc.finalize_cache(session_dir, "aborted")

    data = nsc.read_session_json(session_dir)
    assert data["result"] == "aborted"
    assert data["verification"] is None


# ── read_cached_file / apply_cached_file ───────────────────────────────

def test_read_cached_file(tmp_path):
    session_dir = nsc.init_cache_dir(tmp_path, "test-id", "abc", "test")
    nsc.cache_edit(session_dir, "lib/mod.py", "original", "modified")

    content = nsc.read_cached_file(session_dir, "lib/mod.py")
    assert content == "modified"


def test_read_cached_file_not_found(tmp_path):
    session_dir = nsc.init_cache_dir(tmp_path, "test-id", "abc", "test")
    assert nsc.read_cached_file(session_dir, "nonexistent.py") is None


def test_apply_cached_file(tmp_path):
    session_dir = nsc.init_cache_dir(tmp_path, "test-id", "abc", "test")
    nsc.cache_edit(session_dir, "lib/mod.py", "original", "modified")

    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "lib").mkdir(parents=True)

    result = nsc.apply_cached_file(session_dir, "lib/mod.py", project_root)
    assert result is True
    assert (project_root / "lib" / "mod.py").read_text() == "modified"


def test_apply_cached_file_not_found(tmp_path):
    session_dir = nsc.init_cache_dir(tmp_path, "test-id", "abc", "test")
    result = nsc.apply_cached_file(session_dir, "nonexistent.py", tmp_path)
    assert result is False


# ── list_sessions / get_session_detail ─────────────────────────────────

def test_list_sessions_empty(tmp_path):
    assert nsc.list_sessions(tmp_path) == []


def test_list_sessions(tmp_path):
    nsc.init_cache_dir(tmp_path, "sess1", "abc", "Session 1")
    nsc.init_cache_dir(tmp_path, "sess2", "def", "Session 2")
    nsc.finalize_cache(tmp_path / nsc.CACHE_DIR_NAME / "sess1", "passed")

    sessions = nsc.list_sessions(tmp_path)
    assert len(sessions) == 2
    assert sessions[0]["session_id"] == "sess2"  # sorted reverse by dir name
    assert sessions[0]["result"] == "in_progress"
    assert sessions[1]["result"] == "passed"


def test_get_session_detail(tmp_path):
    session_dir = nsc.init_cache_dir(tmp_path, "sess1", "abc", "Test")
    nsc.cache_edit(session_dir, "lib/x.py", "orig", "mod")
    nsc.finalize_cache(session_dir, "passed", {"smoke_test_passed": True})

    detail = nsc.get_session_detail(tmp_path, "sess1")
    assert detail is not None
    assert detail["session_id"] == "sess1"
    assert detail["result"] == "passed"
    assert "lib/x.py" in detail["_cached_files"]
    assert "lib/x.py.orig" in detail["_cached_files"]


def test_get_session_detail_not_found(tmp_path):
    assert nsc.get_session_detail(tmp_path, "nonexistent") is None


# ── reset_cache ────────────────────────────────────────────────────────

def test_reset_cache_all(tmp_path):
    nsc.init_cache_dir(tmp_path, "sess1", "abc", "s1")
    nsc.init_cache_dir(tmp_path, "sess2", "def", "s2")

    result = nsc.reset_cache(tmp_path)
    assert result["deleted"] == 2
    assert not list((tmp_path / nsc.CACHE_DIR_NAME).iterdir())


def test_reset_cache_specific(tmp_path):
    nsc.init_cache_dir(tmp_path, "sess1", "abc", "s1")
    nsc.init_cache_dir(tmp_path, "sess2", "def", "s2")

    result = nsc.reset_cache(tmp_path, session_id="sess1")
    assert result["deleted"] == 1
    assert result["sessions"][0]["session_id"] == "sess1"

    remaining = list((tmp_path / nsc.CACHE_DIR_NAME).iterdir())
    assert len(remaining) == 1
    assert remaining[0].name == "sess2"


def test_reset_cache_keep_failed(tmp_path):
    s1 = nsc.init_cache_dir(tmp_path, "sess1", "abc", "s1")
    s2 = nsc.init_cache_dir(tmp_path, "sess2", "def", "s2")
    s3 = nsc.init_cache_dir(tmp_path, "sess3", "ghi", "s3")
    nsc.finalize_cache(s1, "passed")
    nsc.finalize_cache(s2, "rolled_back")
    nsc.finalize_cache(s3, "aborted")

    result = nsc.reset_cache(tmp_path, keep_failed=True)
    assert result["deleted"] == 2
    assert result["kept"] == 1

    remaining = [d.name for d in (tmp_path / nsc.CACHE_DIR_NAME).iterdir()]
    assert remaining == ["sess2"]  # only rolled_back survives


def test_reset_cache_dry_run(tmp_path):
    nsc.init_cache_dir(tmp_path, "sess1", "abc", "s1")

    result = nsc.reset_cache(tmp_path, dry_run=True)
    assert result["deleted"] == 1
    # File should still be there
    assert (tmp_path / nsc.CACHE_DIR_NAME / "sess1").exists()


def test_reset_cache_nonexistent(tmp_path):
    result = nsc.reset_cache(tmp_path)
    assert result["deleted"] == 0
    assert result["sessions"] == []


def test_reset_cache_specific_not_found(tmp_path):
    nsc.init_cache_dir(tmp_path, "sess1", "abc", "s1")
    result = nsc.reset_cache(tmp_path, session_id="not-there")
    assert "error" in result
    assert "not found" in result["error"]


# ── cleanup_old_sessions ───────────────────────────────────────────────

def test_cleanup_old_sessions(tmp_path):
    # Create a session with old timestamp by faking the session.json
    s1 = nsc.init_cache_dir(tmp_path, "old-sess", "abc", "Old session")
    nsc.finalize_cache(s1, "passed")

    # Overwrite ended_at to be 10 days ago
    sj_path = s1 / "session.json"
    data = json.loads(sj_path.read_text())
    data["ended_at"] = "2020-01-01T00:00:00Z"
    sj_path.write_text(json.dumps(data))

    # Create a recent session
    s2 = nsc.init_cache_dir(tmp_path, "recent-sess", "def", "Recent session")
    nsc.finalize_cache(s2, "passed")

    deleted = nsc.cleanup_old_sessions(tmp_path, max_age_days=7)
    assert deleted == 1

    remaining = [d.name for d in (tmp_path / nsc.CACHE_DIR_NAME).iterdir()]
    assert "old-sess" not in remaining
    assert "recent-sess" in remaining


def test_cleanup_old_or_empty_cache(tmp_path):
    """No cache directory → no error."""
    assert nsc.cleanup_old_sessions(tmp_path / "nonexistent") == 0


# ── register_commit ────────────────────────────────────────────────────

def test_register_commit(tmp_path):
    session_dir = nsc.init_cache_dir(tmp_path, "test-id", "abc", "test")
    nsc.register_commit(session_dir, "a1b2c3", "nami: fix something")

    data = nsc.read_session_json(session_dir)
    assert len(data["commits"]) == 1
    assert data["commits"][0]["hash"] == "a1b2c3"
    assert data["commits"][0]["message"] == "nami: fix something"

    nsc.register_commit(session_dir, "d4e5f6", "nami: fix another")
    data = nsc.read_session_json(session_dir)
    assert len(data["commits"]) == 2
