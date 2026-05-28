"""
Tests for lib/services/session_manager.py — Git-based rollback safety net.
Also tests for OllamaTools/verify_session.py pytest output parsing.
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Load session_manager directly to bypass lib.services.__init__ import chain
_spec = importlib.util.spec_from_file_location(
    "session_manager",
    Path(__file__).parent.parent / "lib" / "services" / "session_manager.py",
)
_sm = importlib.util.module_from_spec(_spec)
sys.modules["session_manager"] = _sm
_spec.loader.exec_module(_sm)

# Load _parse_pytest_output from verify_session module
_verify_spec = importlib.util.spec_from_file_location(
    "verify_session",
    Path(__file__).parent.parent / "OllamaTools" / "verify_session.py",
)
_verify_mod = importlib.util.module_from_spec(_verify_spec)
_verify_spec.loader.exec_module(_verify_mod)
_parse_pytest_output = _verify_mod._parse_pytest_output

SAFE_POINT_TAG = _sm.SAFE_POINT_TAG
SESSION_FILE = _sm.SESSION_FILE
_ensure_git_repo = _sm._ensure_git_repo
_get_head_sha = _sm._get_head_sha
_tag_exists = _sm._tag_exists
create_safe_point = _sm.create_safe_point
create_session_marker = _sm.create_session_marker
delete_session_marker = _sm.delete_session_marker
move_safe_point_to_head = _sm.move_safe_point_to_head
read_session_marker = _sm.read_session_marker
recover_from_crash = _sm.recover_from_crash
rollback_to_safe_point = _sm.rollback_to_safe_point


@pytest.fixture
def git_repo():
    """Create a temporary directory with a git repo and one committed file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "test"], check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@test"], check=True
        )
        (repo / "hello.py").write_text("print('hello')")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "init"], check=True
        )
        yield repo


# ── Git repo assurance ──────────────────────────────────────────────────

class TestEnsureGitRepo:
    def test_creates_repo_if_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "some_file.py").write_text("x = 1")
            _ensure_git_repo(d)
            assert (d / ".git").is_dir()
            assert _get_head_sha(d) != ""

    def test_noop_if_already_git(self, git_repo):
        sha_before = _get_head_sha(git_repo)
        _ensure_git_repo(git_repo)
        assert _get_head_sha(git_repo) == sha_before


# ── Safe point tag ──────────────────────────────────────────────────────

class TestSafePoint:
    def test_create_safe_point_sets_tag(self, git_repo):
        sha = create_safe_point(git_repo)
        assert sha == _get_head_sha(git_repo)
        assert _tag_exists(git_repo, SAFE_POINT_TAG)

    def test_create_safe_point_moves_existing_tag(self, git_repo):
        create_safe_point(git_repo)
        (git_repo / "new.txt").write_text("change")
        subprocess.run(["git", "-C", str(git_repo), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-m", "v2"], check=True
        )
        new_sha = create_safe_point(git_repo)
        assert new_sha == _get_head_sha(git_repo)

    def test_move_safe_point_to_head(self, git_repo):
        create_safe_point(git_repo)
        (git_repo / "new.txt").write_text("change")
        subprocess.run(["git", "-C", str(git_repo), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-m", "v2"], check=True
        )
        moved_sha = move_safe_point_to_head(git_repo)
        assert moved_sha == _get_head_sha(git_repo)

    def test_rollback_to_safe_point(self, git_repo):
        create_safe_point(git_repo)
        original = _get_head_sha(git_repo)

        (git_repo / "new.txt").write_text("bad change")
        subprocess.run(["git", "-C", str(git_repo), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-m", "bad"], check=True
        )

        assert rollback_to_safe_point(git_repo)
        assert _get_head_sha(git_repo) == original
        assert not (git_repo / "new.txt").exists()

    def test_rollback_no_tag_returns_false(self, git_repo):
        assert not rollback_to_safe_point(git_repo)


# ── Session marker file ─────────────────────────────────────────────────

class TestSessionMarker:
    def test_create_read_delete_cycle(self, git_repo):
        assert read_session_marker(git_repo) is None

        create_session_marker(git_repo, "abc123", "test session")
        data = read_session_marker(git_repo)
        assert data is not None
        assert data["safe_point"] == "abc123"
        assert data["description"] == "test session"
        assert "session_start" in data

        assert delete_session_marker(git_repo)
        assert read_session_marker(git_repo) is None

    def test_delete_nonexistent_returns_false(self, git_repo):
        assert not delete_session_marker(git_repo)

    def test_read_corrupted_file(self, git_repo):
        (git_repo / SESSION_FILE).write_text("not json")
        assert read_session_marker(git_repo) is None


# ── Crash recovery ──────────────────────────────────────────────────────

class TestCrashRecovery:
    def test_no_marker_no_action(self, git_repo):
        assert not recover_from_crash(git_repo)

    def test_marker_exists_triggers_rollback(self, git_repo):
        create_safe_point(git_repo)
        original_sha = _get_head_sha(git_repo)

        (git_repo / "bad.py").write_text("crash")
        subprocess.run(["git", "-C", str(git_repo), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-m", "bad"], check=True
        )

        create_session_marker(git_repo, original_sha, "dangerous edit")

        assert recover_from_crash(git_repo)
        assert _get_head_sha(git_repo) == original_sha
        assert not (git_repo / SESSION_FILE).exists()
        assert not (git_repo / "bad.py").exists()

    def test_marker_with_nonexistent_tag(self, git_repo):
        create_session_marker(git_repo, "deadbeef", "session with bad tag")
        # Should not raise, just log and delete marker
        result = recover_from_crash(git_repo)
        # No tag exists, so reset fails but marker is still deleted
        assert not (git_repo / SESSION_FILE).exists()

    def test_marker_no_description(self, git_repo):
        create_safe_point(git_repo)
        sha = _get_head_sha(git_repo)
        create_session_marker(git_repo, sha, "")
        assert recover_from_crash(git_repo)
        assert not (git_repo / SESSION_FILE).exists()


# ── Empty repo edge cases ───────────────────────────────────────────────

class TestEmptyRepo:
    def test_create_safe_point_on_empty_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            subprocess.run(["git", "-C", str(d), "init", "-q"], check=True)
            assert create_safe_point(d) == ""

    def test_move_safe_point_on_empty_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            subprocess.run(["git", "-C", str(d), "init", "-q"], check=True)
            assert move_safe_point_to_head(d) == ""


# ── pytest output parsing ─────────────────────────────────────────────


class TestParsePytestOutput:
    """Tests for _parse_pytest_output — parsing pytest stdout for test counts."""

    def test_all_passing(self):
        output = "tests/test_foo.py::test_bar PASSED\n\n======= 5 passed in 2.34s ======="
        result = _parse_pytest_output(output)
        assert result["passed"] == 5
        assert result["failed"] == 0
        assert result["errors"] == 0
        assert result["total"] == 5

    def test_some_failed(self):
        output = (
            "tests/test_foo.py::test_a PASSED\ntests/test_foo.py::test_b FAILED\n"
            "======= 1 passed, 1 failed in 1.00s ======="
        )
        result = _parse_pytest_output(output)
        assert result["passed"] == 1
        assert result["failed"] == 1
        assert result["errors"] == 0
        assert result["total"] == 2

    def test_with_errors(self):
        output = (
            "tests/test_foo.py::test_a ERROR\ntests/test_foo.py::test_b PASSED\n"
            "======= 1 passed, 1 error in 0.50s ======="
        )
        result = _parse_pytest_output(output)
        assert result["passed"] == 1
        assert result["failed"] == 0
        assert result["errors"] == 1
        assert result["total"] == 2

    def test_failure_lines_collected(self):
        output = (
            "FAILED tests/test_a.py::test_x - AssertionError: ...\n"
            "FAILED tests/test_b.py::test_y - ValueError: ...\n"
            "======= 1 passed, 2 failed in 1.00s ======="
        )
        result = _parse_pytest_output(output)
        assert len(result["failures"]) == 2
        assert "tests/test_a.py::test_x" in result["failures"][0]
        assert "tests/test_b.py::test_y" in result["failures"][1]

    def test_empty_output(self):
        result = _parse_pytest_output("")
        assert result["passed"] == 0
        assert result["failed"] == 0
        assert result["total"] == 0

    def test_no_tests_collected(self):
        output = "collected 0 items\n\n======= no tests ran in 0.01s ======="
        result = _parse_pytest_output(output)
        assert result["total"] == 0
