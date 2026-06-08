"""
nami_session_cleanup.py — Garbage collection for Nami session cache.

Provides reset_cache (delete sessions with filtering) and cleanup_old_sessions
(prune sessions older than a configurable age).
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from datetime import datetime as dt
from pathlib import Path

from lib.services.nami_session_cache import _cache_root, read_session_json


def reset_cache(
    project_root: Path | None = None,
    session_id: str = "",
    keep_failed: bool = False,
    dry_run: bool = False,
) -> dict:
    """Delete cached sessions, with filtering options."""
    cache_root = _cache_root(project_root)
    if not cache_root.exists():
        return {"deleted": 0, "kept": 0, "sessions": []}

    if session_id:
        target_dir = cache_root / session_id
        if not target_dir.exists():
            return {"deleted": 0, "kept": 0, "sessions": [], "error": f"Session '{session_id}' not found"}
        data = read_session_json(target_dir)
        info = {
            "session_id": session_id,
            "result": data.get("result", "unknown") if data else "unknown",
        }
        if not dry_run:
            shutil.rmtree(target_dir)
        return {"deleted": 1, "kept": 0, "sessions": [info]}

    deleted = []
    kept = []
    for session_dir in sorted(cache_root.iterdir()):
        data = read_session_json(session_dir)
        result = data.get("result", "unknown") if data else "unknown"
        info = {"session_id": session_dir.name, "result": result}

        if keep_failed and result == "rolled_back":
            kept.append(info)
            continue

        deleted.append(info)
        if not dry_run:
            shutil.rmtree(session_dir)

    return {"deleted": len(deleted), "kept": len(kept), "sessions": deleted}


def cleanup_old_sessions(project_root: Path | None = None, max_age_days: int = 7) -> int:
    """Delete session caches older than max_age_days. Returns count of deleted."""
    cache_root = _cache_root(project_root)
    if not cache_root.exists():
        return 0

    cutoff = time.time() - (max_age_days * 86400)
    deleted = 0
    for session_dir in cache_root.iterdir():
        sj = session_dir / "session.json"
        if not sj.exists():
            # Orphan directory — still clean it up if old enough
            try:
                mtime = session_dir.stat().st_mtime
                if mtime < cutoff:
                    shutil.rmtree(session_dir)
                    deleted += 1
            except OSError:
                continue
            continue

        try:
            data = json.loads(sj.read_text())
            ended = data.get("ended_at", data.get("started_at"))
            if not ended:
                # Use directory mtime as fallback
                if session_dir.stat().st_mtime < cutoff:
                    shutil.rmtree(session_dir)
                    deleted += 1
                continue

            # Parse ISO 8601 timestamp
            try:
                ended_dt = dt.fromisoformat(ended.replace("Z", "+00:00"))
                if ended_dt.timestamp() < cutoff:
                    shutil.rmtree(session_dir)
                    deleted += 1
                    logging.info(f"[cache] Cleaned up: {session_dir.name}")
            except (ValueError, OSError):
                continue
        except Exception:
            logging.exception("Failed to clean up session %s", session_dir.name)

    return deleted
