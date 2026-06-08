"""
nami_session_cache.py — Session metadata management for Nami self-modification.

Handles session JSON CRUD (init, read, write, finalize) and session listing.
File I/O is in nami_session_io.py. Garbage collection is in nami_session_cleanup.py.

Directory structure:
    .nami_session_cache/
    ├── 2026-05-27T19-30-00_refactor-memory/
    │   ├── session.json
    │   ├── lib/services/memory_service.py       ← modified
    │   ├── lib/services/memory_service.py.orig  ← original
    │   └── ...
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from lib.utils import resolve_project_root, slugify

CACHE_DIR_NAME = ".nami_session_cache"

# ── Helpers ───────────────────────────────────────────────────────────────

def _cache_root(project_root: Path | None = None) -> Path:
    project_root = project_root or resolve_project_root()
    return project_root / CACHE_DIR_NAME


def generate_session_id(description: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    slug = slugify(description, max_length=50)
    return f"{ts}_{slug}"


# ── Session JSON ──────────────────────────────────────────────────────────

def read_session_json(session_dir: Path) -> dict | None:
    sj = session_dir / "session.json"
    if not sj.exists():
        return None
    try:
        return json.loads(sj.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def write_session_json(session_dir: Path, data: dict) -> None:
    sj = session_dir / "session.json"
    sj.write_text(json.dumps(data, indent=2))


def init_cache_dir(project_root: Path, session_id: str, safe_point: str, description: str) -> Path:
    """Create the cache directory for a session and initialize session.json."""
    cache_root = _cache_root(project_root)
    session_dir = cache_root / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    data = {
        "session_id": session_id,
        "description": description,
        "safe_point": safe_point,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ended_at": None,
        "result": "in_progress",
        "commits": [],
        "verification": None,
        "changed_files": [],
    }
    write_session_json(session_dir, data)
    return session_dir


def finalize_cache(session_dir: Path, result: str, verification: dict | None = None) -> None:
    """Mark a session as completed (passed, rolled_back, or aborted)."""
    data = read_session_json(session_dir)
    if not data:
        return
    data["ended_at"] = datetime.now(timezone.utc).isoformat()
    data["result"] = result
    if verification:
        data["verification"] = verification
    write_session_json(session_dir, data)


# ── Session listing ───────────────────────────────────────────────────────

def list_sessions(project_root: Path | None = None) -> list[dict]:
    """Return all cached sessions with summary info."""
    cache_root = _cache_root(project_root)
    if not cache_root.exists():
        return []

    sessions = []
    for session_dir in sorted(cache_root.iterdir(), reverse=True):
        data = read_session_json(session_dir)
        if not data:
            continue
        sessions.append({
            "session_id": data.get("session_id", session_dir.name),
            "description": data.get("description", ""),
            "result": data.get("result", "unknown"),
            "started_at": data.get("started_at", ""),
            "ended_at": data.get("ended_at", ""),
            "changed_files": data.get("changed_files", []),
        })
    return sessions


def get_session_detail(project_root: Path | None, session_id: str) -> dict | None:
    """Get full session.json + file listing for a session."""
    cache_root = _cache_root(project_root)
    session_dir = cache_root / session_id
    if not session_dir.exists():
        return None

    data = read_session_json(session_dir)
    if not data:
        return None

    files = [
        str(p.relative_to(session_dir))
        for p in session_dir.rglob("*")
        if p.is_file() and p.name != "session.json"
    ]
    data["_cached_files"] = sorted(files)
    data["_cache_size_bytes"] = sum(
        (f.stat().st_size for f in session_dir.rglob("*") if f.is_file()),
        0,
    )
    return data

