"""
nami_session_cache.py — Persistent session cache for Nami self-modification.

Stores original + modified files from each edit session so that failed
changes survive `git reset --hard` for later debugging and retry.

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
import logging
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

CACHE_DIR_NAME = ".nami_session_cache"

# ── Helpers ───────────────────────────────────────────────────────────────

def _cache_root(project_root: Path | None = None) -> Path:
    project_root = project_root or _resolve_project_root()
    return project_root / CACHE_DIR_NAME


def _resolve_project_root() -> Path:
    for anchor in [Path("/workspace/project/nami_ai"), Path.cwd()]:
        if (anchor / "api_server.py").exists():
            return anchor
    current = Path.cwd()
    for _ in range(5):
        if (current / "api_server.py").exists():
            return current
        current = current.parent
    return Path.cwd()


def _slugify(text: str) -> str:
    """Convert a description into a filesystem-safe short slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = slug[:50].rstrip("-")
    return slug or "unnamed"


def generate_session_id(description: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    slug = _slugify(description)
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


# ── File caching ──────────────────────────────────────────────────────────

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


# ── Cache reset / cleanup ─────────────────────────────────────────────────

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
            from datetime import datetime as dt
            try:
                ended_dt = dt.fromisoformat(ended.replace("Z", "+00:00"))
                if ended_dt.timestamp() < cutoff:
                    shutil.rmtree(session_dir)
                    deleted += 1
                    logging.info(f"[cache] Cleaned up: {session_dir.name}")
            except (ValueError, OSError):
                continue
        except Exception:
            continue

    return deleted
