"""
Tests for lib/services/sandbox_manager.py

Covers:
- _truncate(): short/long/multibyte/empty/boundary/zero inputs
- get_sandbox_password(): env var, file, config, None, whitespace, OSError
- SandboxJob: is_running, get_output, elapsed_seconds, notified, exit_code
"""

import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

# Import sandbox_manager directly to avoid loading lib.services.__init__
# which pulls in sentence_transformers, colorama, and other heavy deps.
_saved_sandbox = sys.modules.get("sandbox_manager")
try:
    _sandbox_path = Path(__file__).parent.parent / "lib" / "services" / "sandbox_manager.py"
    _spec = importlib.util.spec_from_file_location("sandbox_manager", _sandbox_path)
    _sandbox = importlib.util.module_from_spec(_spec)
    sys.modules["sandbox_manager"] = _sandbox
    _spec.loader.exec_module(_sandbox)

    get_sandbox_password = _sandbox.get_sandbox_password
    _truncate = _sandbox._truncate
    SandboxJob = _sandbox.SandboxJob
finally:
    # Restore or remove the custom "sandbox_manager" key so other tests
    # importing lib.services.sandbox_manager see the real module.
    if _saved_sandbox is None:
        sys.modules.pop("sandbox_manager", None)
    else:
        sys.modules["sandbox_manager"] = _saved_sandbox


def test_truncate_short_text():
    """Short text within max_bytes → returned unchanged"""
    result = _truncate("hello", max_bytes=100)
    assert result == "hello", f"[FAIL] Expected 'hello', got {result!r}"


def test_truncate_long_text():
    """Long text exceeding max_bytes → truncated with marker suffix"""
    suffix = "\n[... output truncated]"
    result = _truncate("hello world this is long", max_bytes=11)
    assert result.endswith(suffix) and len(result.encode("utf-8")) <= 11 + len(suffix.encode("utf-8")), f"[FAIL] Unexpected truncation result: {result!r}"


def test_truncate_multibyte_boundary():
    """Multi-byte UTF-8 at truncation boundary → handled gracefully"""
    text = "a" * 5 + "日本語"  # 5 bytes ASCII + 9 bytes multi-byte
    result = _truncate(text, max_bytes=6)  # cuts into middle of multi-byte char
    assert isinstance(result, str), "Result should be a string"


def test_truncate_empty_string():
    """Empty string → returned unchanged"""
    result = _truncate("", max_bytes=100)
    assert result == "", f"[FAIL] Expected '', got {result!r}"


def test_truncate_max_bytes_zero():
    """max_bytes=0 → appends truncation marker"""
    suffix = "\n[... output truncated]"
    result = _truncate("hello", max_bytes=0)
    assert result == suffix, f"[FAIL] Expected truncation marker, got {result!r}"


def test_truncate_at_exact_boundary():
    """Text exactly at max_bytes boundary → returned unchanged"""
    text = "hello"  # 5 bytes
    result = _truncate(text, max_bytes=5)
    assert result == "hello", f"[FAIL] Expected 'hello', got {result!r}"


def test_env_var_highest_priority():
    """SANDBOX_PASSWORD env var set → returns env value"""
    with patch.dict(os.environ, {"SANDBOX_PASSWORD": "env_secret"}, clear=True):
        result = get_sandbox_password()
        assert result == "env_secret", f"[FAIL] Expected 'env_secret', got {result!r}"


def test_file_content_fallback():
    """Env not set, file exists → returns file content"""
    with patch.dict(os.environ, {}, clear=True), \
         patch.object(Path, "is_file", return_value=True), \
         patch.object(Path, "read_text", return_value="file_secret\n"):
        result = get_sandbox_password()
        assert result == "file_secret", f"[FAIL] Expected 'file_secret', got {result!r}"


def test_config_value_fallback():
    """Env not set, file doesn't exist → uses config_password"""
    with patch.dict(os.environ, {}, clear=True), \
         patch.object(Path, "is_file", return_value=False):
        result = get_sandbox_password(config_password="config_val")
        assert result == "config_val", f"[FAIL] Expected 'config_val', got {result!r}"


def test_none_available_returns_none():
    """No sources → returns None"""
    with patch.dict(os.environ, {}, clear=True), \
         patch.object(Path, "is_file", return_value=False):
        result = get_sandbox_password()
        assert result is None, f"[FAIL] Expected None, got {result!r}"


def test_whitespace_env_falls_through():
    """Empty/whitespace env var → falls through to next source"""
    with patch.dict(os.environ, {"SANDBOX_PASSWORD": "   "}, clear=True), \
         patch.object(Path, "is_file", return_value=True), \
         patch.object(Path, "read_text", return_value="file_secret"):
        result = get_sandbox_password()
        assert result == "file_secret", f"[FAIL] Expected 'file_secret', got {result!r}"


def test_empty_file_falls_through():
    """Empty file → falls through to next source"""
    with patch.dict(os.environ, {}, clear=True), \
         patch.object(Path, "is_file", return_value=True), \
         patch.object(Path, "read_text", return_value="   \n"):
        result = get_sandbox_password(config_password="config_val")
        assert result == "config_val", f"[FAIL] Expected 'config_val', got {result!r}"


def test_oserror_on_read_falls_through():
    """File read OSError → gracefully falls through to next source"""
    with patch.dict(os.environ, {}, clear=True), \
         patch.object(Path, "is_file", return_value=True), \
         patch.object(Path, "read_text", side_effect=OSError("Permission denied")):
        result = get_sandbox_password(config_password="config_val")
        assert result == "config_val", f"[FAIL] Expected 'config_val', got {result!r}"


# ── SandboxJob tests ──────────────────────────────────────────────


def test_job_is_running_when_created():
    """Newly created job → is_running is True, finished_at is None"""
    job = SandboxJob("test-1", "echo hello")
    assert job.is_running and job.finished_at is None, f"[FAIL] is_running={job.is_running}, finished_at={job.finished_at}"


def test_job_is_running_false_after_finish():
    """After setting finished_at → is_running is False"""
    job = SandboxJob("test-2", "echo hello")
    job.finished_at = datetime.now(timezone.utc)
    assert not job.is_running, f"[FAIL] is_running still True after finished_at set"


def test_get_output_reflects_buffer():
    """get_output() reflects what was written to the StringIO buffer"""
    job = SandboxJob("test-3", "echo hello")
    job.output.write("line 1\n")
    job.output.write("line 2\n")
    result = job.get_output()
    assert result == "line 1\nline 2\n", f"[FAIL] Expected 'line 1\\nline 2\\n', got {result!r}"


def test_get_output_empty_by_default():
    """get_output() on fresh job returns empty string"""
    job = SandboxJob("test-4", "echo hello")
    result = job.get_output()
    assert result == "", f"[FAIL] Expected '', got {result!r}"


def test_elapsed_seconds_with_finished_at():
    """elapsed_seconds with finished_at → returns diff to started_at"""
    job = SandboxJob("test-5", "echo hello")
    now = datetime.now(timezone.utc)
    job.started_at = now - timedelta(seconds=10)
    job.finished_at = now - timedelta(seconds=5)
    elapsed = job.elapsed_seconds()
    assert 4.9 <= elapsed <= 5.1, f"[FAIL] Expected ~5.0, got {elapsed}"


def test_elapsed_seconds_without_finished_at():
    """elapsed_seconds without finished_at → returns elapsed from started_at"""
    job = SandboxJob("test-6", "echo hello")
    job.started_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    time.sleep(0.05)
    elapsed = job.elapsed_seconds()
    assert 2.0 <= elapsed <= 2.2, f"[FAIL] Expected ~2.05, got {elapsed}"


def test_notified_starts_false():
    """notified flag starts as False"""
    job = SandboxJob("test-7", "echo hello")
    assert job.notified is False, f"[FAIL] Expected notified=False, got {job.notified}"


def test_job_stores_command_and_id():
    """SandboxJob stores job_id and command correctly"""
    job = SandboxJob("myjob-42", "ls -la /tmp")
    assert job.job_id == "myjob-42" and job.command == "ls -la /tmp", f"[FAIL] job_id={job.job_id}, command={job.command}"


def test_exit_code_starts_none():
    """exit_code starts as None on fresh job"""
    job = SandboxJob("test-9", "echo hello")
    assert job.exit_code is None, f"[FAIL] Expected exit_code=None, got {job.exit_code}"
