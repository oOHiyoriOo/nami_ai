"""
nami_verify_session — Run tests and finalize a self-modification session.

Runs a smoke test + full pytest suite. On PASS, moves the safe_point tag
forward to HEAD and deletes the session marker. On FAIL, rolls back to the
safe_point tag and deletes the session marker.
"""

import json
import logging
import re
import subprocess
import sys
from pathlib import Path

from lib.services.nami_session_cache import (
    _cache_root,
    finalize_cache,
    read_session_json,
)
from lib.services.session_manager import (
    _resolve_project_root,
    delete_session_marker,
    move_safe_point_to_head,
    read_session_marker,
    rollback_to_safe_point,
)

SMOKE_TEST_CMD = [sys.executable, "-c", "import lib.services.event_bus; import lib.global_registry"]
SMOKE_TIMEOUT = 5

FULL_TEST_CMD = [sys.executable, "-m", "pytest", "tests/", "-x", "--timeout=60"]
FULL_TEST_TIMEOUT = 120


def _format_pytest_summary(full_suite: dict) -> str:
    """Format pytest results as a human-readable one-liner."""
    tc = full_suite.get("test_counts", {})
    passed = tc.get("passed", 0)
    failed = tc.get("failed", 0)
    total = tc.get("total", 0)
    errors = tc.get("errors", 0)
    parts = [f"{passed} passed", f"{failed} failed"]
    if errors:
        parts.append(f"{errors} errors")
    return f"{', '.join(parts)} in {tc.get('time', '?.??s')}"


def _parse_pytest_output(stdout: str) -> dict:
    """Parse pytest stdout for test counts and failures."""
    result = {
        "passed": 0,
        "failed": 0,
        "total": 0,
        "errors": 0,
        "failures": [],
    }

    # Match "X passed, Y failed" or "X passed"
    summary_match = re.search(r"(\d+)\s+passed", stdout)
    if summary_match:
        result["passed"] = int(summary_match.group(1))

    failed_match = re.search(r"(\d+)\s+failed", stdout)
    if failed_match:
        result["failed"] = int(failed_match.group(1))

    error_match = re.search(r"(\d+)\s+errors?", stdout)
    if error_match:
        result["errors"] = int(error_match.group(1))

    result["total"] = result["passed"] + result["failed"] + result["errors"]

    # Collect failure lines
    for line in stdout.split("\n"):
        if "FAILED" in line and "::" in line:
            result["failures"].append(line.strip())

    return result


async def verify_session() -> str:
    """
    Verify the current self-modification session by running tests.

    Runs a smoke test first, then the full pytest suite. Ends the session
    on PASS (move safe_point forward) or FAIL (rollback to safe_point).

    No active session → error.
    """
    project_root = _resolve_project_root()

    existing = read_session_marker(project_root)
    if existing is None:
        return json.dumps({
            "ok": False,
            "error": "No active change session found. Start one with nami_begin_session first.",
        }, indent=2)

    session_desc = existing.get("description", "unknown")
    results = {
        "session_description": session_desc,
        "smoke_test": None,
        "full_suite": None,
    }

    # ── Smoke test ────────────────────────────────────────────────────
    try:
        smoke = subprocess.run(
            SMOKE_TEST_CMD,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=SMOKE_TIMEOUT,
        )
        results["smoke_test"] = {
            "passed": smoke.returncode == 0,
            "returncode": smoke.returncode,
            "stdout": smoke.stdout[:500] if smoke.stdout else "",
            "stderr": smoke.stderr[:500] if smoke.stderr else "",
        }
    except subprocess.TimeoutExpired:
        results["smoke_test"] = {
            "passed": False,
            "error": f"Smoke test timed out after {SMOKE_TIMEOUT}s",
        }
    except Exception as e:
        results["smoke_test"] = {
            "passed": False,
            "error": f"Smoke test failed: {e}",
        }

    # If smoke test fails, still run full suite (don't short-circuit)

    # ── Full pytest suite ─────────────────────────────────────────────
    try:
        full = subprocess.run(
            FULL_TEST_CMD,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=FULL_TEST_TIMEOUT,
        )
        parsed = _parse_pytest_output(full.stdout + full.stderr)
        results["full_suite"] = {
            "passed": full.returncode == 0,
            "returncode": full.returncode,
            "test_counts": parsed,
        }
    except subprocess.TimeoutExpired:
        results["full_suite"] = {
            "passed": False,
            "error": f"Full test suite timed out after {FULL_TEST_TIMEOUT}s",
            "test_counts": {"passed": 0, "failed": 0, "total": 0, "errors": 0, "failures": ["TIMEOUT"]},
        }
    except Exception as e:
        results["full_suite"] = {
            "passed": False,
            "error": f"Full test suite failed to execute: {e}",
            "test_counts": {"passed": 0, "failed": 0, "total": 0, "errors": 0, "failures": [str(e)]},
        }

    # ── Decide verdict ────────────────────────────────────────────────
    suite_passed = results["full_suite"].get("passed", False)
    smoke_passed = results["smoke_test"].get("passed", False) if results["smoke_test"] else False
    overall_pass = suite_passed and smoke_passed

    # ── Cache session data ────────────────────────────────────────────────
    session_id = existing.get("session_id", "")
    cache_dir = _cache_root(project_root) / session_id if session_id else None

    if cache_dir and cache_dir.exists():
        verif_data = {
            "smoke_test_passed": smoke_passed,
            "pytest_passed": results["full_suite"].get("passed", False),
            "pytest_output": _format_pytest_summary(results["full_suite"]),
            "failed_tests": results["full_suite"].get("test_counts", {}).get("failures", []),
        }
    else:
        verif_data = None

    try:
        if overall_pass:
            new_sha = move_safe_point_to_head(project_root)
            delete_session_marker(project_root)

            if cache_dir and cache_dir.exists():
                finalize_cache(cache_dir, "passed", verif_data)

            return json.dumps({
                "ok": True,
                "verdict": "PASS",
                "safe_point": new_sha,
                "smoke_test_passed": smoke_passed,
                "suite_passed": suite_passed,
                "test_counts": results["full_suite"].get("test_counts", {}),
                "message": f"All tests passed. safe_point moved to {new_sha[:8]}. Session complete.",
            }, indent=2)
        else:
            rollback_ok = rollback_to_safe_point(project_root)
            delete_session_marker(project_root)

            if cache_dir and cache_dir.exists():
                finalize_cache(cache_dir, "rolled_back", verif_data)

            tc = results["full_suite"].get("test_counts", {})
            failure_detail = ""
            if not smoke_passed:
                failure_detail = "Smoke test failed. "
            if not suite_passed:
                failure_detail += f"Full suite: {tc.get('failed', '?')} failed, {tc.get('passed', '?')} passed."

            return json.dumps({
                "ok": True,
                "verdict": "FAIL",
                "rolled_back": rollback_ok,
                "smoke_test_passed": smoke_passed,
                "suite_passed": suite_passed,
                "test_counts": tc,
                "message": (
                    f"Tests failed. Rolled back to safe_point. "
                    f"{failure_detail}"
                ),
            }, indent=2)

    except Exception as e:
        logging.error(f"[session] verify_session failed: {e}")
        return json.dumps({"ok": False, "error": str(e)}, indent=2)


def get_tool() -> list[dict]:
    return [
        {
            "type": "function",
            "safe": False,
            "categories": ["self_modification"],
            "function": {
                "name": "nami_verify_session",
                "description": (
                    "Verify the current self-modification session by running tests. "
                    "Runs a smoke test and the full pytest suite. "
                    "PASS: accepts changes, moves safe_point forward, deletes session marker. "
                    "FAIL: rolls back to safe_point, discards changes, deletes session marker."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            "func": verify_session,
        },
    ]
