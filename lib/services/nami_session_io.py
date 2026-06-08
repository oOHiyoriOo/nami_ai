"""
nami_session_io.py — File-cache I/O for Nami session persistence.

Handles saving original + modified file snapshots to .nami_session_cache/,
reading cached files back, applying them to the working tree, and recording
commits that happen during a session.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from lib.services.nami_session_cache import read_session_json, write_session_json


def cache_edit(
    session_dir: Path,
    rel_file_path: str,
    original_content: str,
    modified_content: str,
) -> None:
    """Save original (.orig) and modified versions of a file to the cache."""
    dest = session_dir / rel_file_path
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Save original
    orig_path = Path(str(dest) + ".orig")
    orig_path.write_text(original_content)

    # Save modified
    dest.write_text(modified_content)

    # Track in session.json
    data = read_session_json(session_dir)
    if data and rel_file_path not in data.get("changed_files", []):
        data.setdefault("changed_files", []).append(rel_file_path)
        write_session_json(session_dir, data)


def register_commit(session_dir: Path, commit_hash: str, commit_message: str) -> None:
    """Record a commit that happened during this session."""
    data = read_session_json(session_dir)
    if not data:
        return
    data.setdefault("commits", []).append({
        "hash": commit_hash,
        "message": commit_message,
    })
    write_session_json(session_dir, data)


def read_cached_file(session_dir: Path, file_path: str) -> str | None:
    """Read a cached (modified) file. Returns content or None."""
    file = session_dir / file_path
    if not file.exists():
        return None
    try:
        return file.read_text()
    except OSError:
        return None


def apply_cached_file(session_dir: Path, file_path: str, project_root: Path) -> bool:
    """Copy a cached file back into the working tree. Returns True on success."""
    src = session_dir / file_path
    dest = project_root / file_path
    if not src.exists():
        return False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return True
    except OSError as e:
        logging.error(f"[cache] apply_cached_file failed: {e}")
        return False
