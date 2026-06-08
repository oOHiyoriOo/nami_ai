"""
session_manager.py — Git-based rollback safety net for Nami self-modification.

Three-layer system:
  1. Local git repo with `nami_safe_point` tag tracking the last verified-good commit
  2. `.nami_change_session` marker file for crash detection
  3. Auto-recovery in app_initializer before any service starts
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from lib.utils import resolve_project_root

SAFE_POINT_TAG = "nami_safe_point"
SESSION_FILE = ".nami_change_session"
BACKUPS_DIR = ".nami_backups"


def _ensure_git_repo(src_dir: Path) -> None:
    """Initialize a local git repo in src_dir if none exists. No remote interaction."""
    git_dir = src_dir / ".git"
    if git_dir.exists():
        return

    subprocess.run(["git", "-C", str(src_dir), "init", "-q"], check=True)
    # Configure local git user for commits
    subprocess.run(
        ["git", "-C", str(src_dir), "config", "user.name", "nami"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(src_dir), "config", "user.email", "nami@ai.local"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(src_dir), "add", "."], check=True
    )
    # Allow initial commit to succeed even if nothing to commit
    result = subprocess.run(
        ["git", "-C", str(src_dir), "commit", "-m", "nami: initial snapshot"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 and "nothing to commit" not in result.stderr:
        result.check_returncode()
    logging.info("[session] Initialized local git repo for rollback safety")


def _get_head_sha(src_dir: Path) -> str:
    """Return the SHA of HEAD, or empty string if no commits yet."""
    result = subprocess.run(
        ["git", "-C", str(src_dir), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _tag_exists(src_dir: Path, tag: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(src_dir), "tag", "-l", tag],
        capture_output=True, text=True,
    )
    return tag in result.stdout.strip().split("\n")


def create_safe_point(src_dir: Path) -> str:
    """Ensure git repo exists and set/move nami_safe_point tag to HEAD."""
    _ensure_git_repo(src_dir)
    sha = _get_head_sha(src_dir)
    if not sha:
        return ""

    # Force-update (or create) the tag
    subprocess.run(
        ["git", "-C", str(src_dir), "tag", "-f", SAFE_POINT_TAG, "HEAD"],
        check=True, capture_output=True,
    )
    logging.info(f"[session] safe_point tag set to {sha[:8]}")
    return sha


def move_safe_point_to_head(src_dir: Path) -> str:
    """After successful verification, move the tag forward to HEAD."""
    sha = _get_head_sha(src_dir)
    if not sha:
        return ""
    subprocess.run(
        ["git", "-C", str(src_dir), "tag", "-f", SAFE_POINT_TAG, "HEAD"],
        check=True, capture_output=True,
    )
    logging.info(f"[session] safe_point tag moved to {sha[:8]} (verification passed)")
    return sha


def rollback_to_safe_point(src_dir: Path) -> bool:
    """Hard reset working tree to the safe_point tag. Returns True on success."""
    if not _tag_exists(src_dir, SAFE_POINT_TAG):
        logging.error("[session] No safe_point tag found — cannot rollback")
        return False

    try:
        subprocess.run(
            ["git", "-C", str(src_dir), "reset", "--hard", SAFE_POINT_TAG],
            check=True, capture_output=True,
        )
        sha = _get_head_sha(src_dir)
        logging.warning(f"[session] Rolled back to safe_point: {sha[:8]}")
        return True
    except subprocess.CalledProcessError as e:
        logging.critical(f"[session] Rollback failed: {e}")
        return False


# ── Session marker file ──────────────────────────────────────────────────

def create_session_marker(
    project_root: Path,
    safe_point_sha: str,
    description: str,
    session_id: str = "",
) -> Path:
    """Write the .nami_change_session marker file. Returns the file path."""
    data = {
        "session_start": datetime.now(timezone.utc).isoformat(),
        "safe_point": safe_point_sha,
        "description": description,
        "session_id": session_id,
    }
    marker_path = project_root / SESSION_FILE
    marker_path.write_text(json.dumps(data, indent=2))
    logging.info(f"[session] Session marker created: {description[:60]}")
    return marker_path


def delete_session_marker(project_root: Path) -> bool:
    """Remove the session marker file. Returns True if it existed."""
    marker_path = project_root / SESSION_FILE
    if marker_path.exists():
        marker_path.unlink()
        logging.info("[session] Session marker deleted")
        return True
    return False


def read_session_marker(project_root: Path) -> dict | None:
    """Read and return the session marker data, or None if absent."""
    marker_path = project_root / SESSION_FILE
    if not marker_path.exists():
        return None
    try:
        return json.loads(marker_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logging.warning(f"[session] Could not read session marker: {e}")
        return None


# ── Crash recovery (called from app_initializer before services start) ───

def recover_from_crash(project_root: Path | None = None) -> bool:
    """
    Check for an orphaned session marker and roll back if found.
    Called BEFORE any service initialization.

    Returns True if recovery was performed, False if no action needed.
    """
    if project_root is None:
        project_root = resolve_project_root()

    marker = read_session_marker(project_root)
    if marker is None:
        return False

    logging.error(
        "[recovery] Change session marker found at startup — "
        "server likely crashed during self-modification. Rolling back."
    )

    recovered = False
    try:
        src_dir = project_root
        safe_point = marker.get("safe_point", SAFE_POINT_TAG)

        if _tag_exists(src_dir, SAFE_POINT_TAG):
            subprocess.run(
                ["git", "-C", str(src_dir), "reset", "--hard", safe_point],
                check=True, capture_output=True,
            )
            recovered = True

        delete_session_marker(project_root)
        session_desc = marker.get("description", "unknown")
        logging.info(
            f"[recovery] Rolled back to {safe_point}. "
            f"Session '{session_desc}' discarded."
        )
    except Exception as e:
        logging.critical(f"[recovery] Auto-rollback failed: {e}")

    return recovered
