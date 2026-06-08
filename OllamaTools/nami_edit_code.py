"""
nami_edit_code — Safe code editing tool with backup, audit-trail, and hot-reload.

Unifies the read→write→reload cycle into a single tool call:
  1. Validate path against edit whitelist
  2. Read file, find old_str, verify uniqueness
  3. Write timestamped backup to .nami_backups/
  4. Write modified file
  5. Store CodeChange node in Neo4j (audit-trail)
  6. If auto_reload: publish system.reload_tools event
  7. Return diff preview
"""

import difflib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from OllamaTools import _require_active_session, require_active_session, tool_error, tool_success
from lib.global_registry import g_data
from lib.services.nami_session_cache import _cache_root
from lib.services.nami_session_io import cache_edit
from lib.services.safe_edit_validator import validate_edit_path

_PROJECT_ROOT = Path("/workspace/project/nami_ai")
_BACKUP_DIR = _PROJECT_ROOT / ".nami_backups"


async def nami_edit_code(
    file_path: str,
    old_str: str,
    new_str: str,
    description: str = "",
    auto_reload: bool = True,
) -> str:
    """
    Edit a file in Nami's own codebase with safe string replacement.

    Args:
        file_path:    Absolute path to the file (e.g. '/workspace/project/nami_ai/lib/services/event_bus.py')
        old_str:      Exact string to find and replace (must be unique in file)
        new_str:      Replacement string
        description:  Human-readable description of the change (for logging/audit)
        auto_reload:  Emit system.reload_tools after successful edit (default: True)

    Returns:
        tool_success with diff preview, or tool_error if old_str not found or not unique.
    """
    # ── Session enforcement ───────────────────────────────────────────
    err = require_active_session()
    if err:
        return err

    # ── Path validation ───────────────────────────────────────────────
    allowed, reason, reload_event = validate_edit_path(file_path)
    if not allowed:
        return tool_error(f"Edit denied: {reason}", path=file_path)

    abs_path = Path(file_path).resolve()

    # Extra safety: ensure path is truly under project root
    try:
        abs_path.relative_to(_PROJECT_ROOT)
    except ValueError:
        return tool_error(
            f"Path '{file_path}' is outside the project root '{_PROJECT_ROOT}'",
            path=file_path,
        )

    if not abs_path.exists():
        return tool_error(f"File not found: {abs_path}", path=file_path)

    # ── Read file ─────────────────────────────────────────────────────
    try:
        original_content = abs_path.read_text()
    except Exception as e:
        return tool_error(f"Failed to read file: {e}", path=file_path)

    # ── Verify old_str uniqueness ─────────────────────────────────────
    count = original_content.count(old_str)
    if count == 0:
        return tool_error(
            f"old_str not found in {file_path}. No changes made.",
            path=file_path,
            old_str_preview=old_str[:200],
        )
    if count > 1:
        return tool_error(
            f"old_str found {count} times in {file_path}. "
            f"Must be unique to avoid accidental multi-replace. "
            f"Provide more context to make the match unique.",
            path=file_path,
            occurrences=count,
            old_str_preview=old_str[:200],
        )

    # ── Write backup ──────────────────────────────────────────────────
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    backup_name = f"{abs_path.name}.{timestamp}.bak"
    backup_path = _BACKUP_DIR / backup_name
    try:
        shutil.copy2(abs_path, backup_path)
    except Exception as e:
        return tool_error(f"Failed to create backup: {e}", path=file_path)

    # ── Write modified file ───────────────────────────────────────────
    modified_content = original_content.replace(old_str, new_str, 1)
    try:
        abs_path.write_text(modified_content)
    except Exception as e:
        # Attempt to restore from backup
        try:
            shutil.copy2(backup_path, abs_path)
        except Exception:
            pass
        return tool_error(f"Failed to write file: {e}", path=file_path)

    # ── Cache for session persistence ─────────────────────────────────
    rel_path = str(abs_path.relative_to(_PROJECT_ROOT))
    session = _require_active_session()
    if session and session.get("session_id"):
        session_dir = _cache_root(_PROJECT_ROOT) / session["session_id"]
        if session_dir.exists():
            cache_edit(session_dir, rel_path, original_content, modified_content)

    # ── Generate diff preview ─────────────────────────────────────────
    diff_lines = list(
        difflib.unified_diff(
            original_content.splitlines(keepends=True),
            modified_content.splitlines(keepends=True),
            fromfile=str(abs_path),
            tofile=str(abs_path),
        )
    )
    diff_preview = "".join(diff_lines)

    # ── Store CodeChange in Neo4j (audit-trail) ───────────────────────
    await _store_code_change(
        file_path=str(abs_path),
        backup_path=str(backup_path),
        description=description or "Unnamed edit",
        diff_preview=diff_preview,
    )

    # ── Auto-reload ───────────────────────────────────────────────────
    if auto_reload and reload_event:
        event_bus = g_data.get("event_bus")
        if event_bus:
            try:
                from lib.services.event_bus import Event

                if reload_event == "system.reload_tools":
                    # For individual tool files, emit module_changed to do
                    # single-tool reload instead of full tool reload.
                    rel = str(abs_path.relative_to(_PROJECT_ROOT))
                    if rel.startswith("OllamaTools/") and rel.endswith(".py"):
                        module_path = "OllamaTools." + Path(rel).stem
                        await event_bus.publish(Event(
                            type="system.module_changed",
                            data={
                                "module_path": module_path,
                                "file_path": str(abs_path),
                                "description": description,
                            },
                        ))
                    else:
                        await event_bus.publish(Event(
                            type="system.reload_tools",
                            data={
                                "file_path": str(abs_path),
                                "description": description,
                            },
                        ))
                elif reload_event.startswith("system.module_changed:"):
                    module_path = reload_event.split(":", 1)[1]
                    await event_bus.publish(Event(
                        type="system.module_changed",
                        data={
                            "module_path": module_path,
                            "file_path": str(abs_path),
                            "description": description,
                        },
                    ))
                else:
                    logging.debug(
                        "[nami_edit_code] Unrecognized reload_event=%r — skipped",
                        reload_event,
                    )
            except Exception as e:
                logging.error(f"[nami_edit_code] Failed to publish reload event: {e}")

    return tool_success(
        f"Edited {file_path}: replaced 1 occurrence ({len(old_str)} → {len(new_str)} chars)",
        path=file_path,
        backup=backup_name,
        replacements=1,
        diff=diff_preview,
    )


async def _store_code_change(
    file_path: str,
    backup_path: str,
    description: str,
    diff_preview: str,
) -> None:
    """Persist a CodeChange node in Neo4j for audit-trail."""
    try:
        db = g_data.get("memory_db")
        if not db:
            logging.warning("[nami_edit_code] memory_db not available — skipping audit")
            return

        driver = db.get_driver()
        async with driver.session() as session:
            await session.run(
                """
                CREATE (c:CodeChange {
                    id: randomUUID(),
                    file_path: $file_path,
                    backup_path: $backup_path,
                    description: $description,
                    diff_preview: $diff_preview,
                    changed_at: $changed_at
                })
                """,
                file_path=file_path,
                backup_path=backup_path,
                description=description,
                diff_preview=diff_preview[:2000],
                changed_at=datetime.now(timezone.utc).isoformat(),
            )
        logging.info(f"[nami_edit_code] CodeChange audit node stored for {file_path}")
    except Exception as e:
        logging.error(f"[nami_edit_code] Failed to store CodeChange: {e}")


def get_tool() -> list[dict]:
    return [{
        "type": "function",
        "safe": False,
        "categories": ["self_modification"],
        "function": {
            "name": "nami_edit_code",
            "description": (
                "Edit a file in Nami's own codebase with safe string replacement. "
                "Finds old_str (must be unique), replaces with new_str, creates a "
                "timestamped backup in .nami_backups/, stores an audit entry, and "
                "optionally triggers hot-reload. Requires an active change session "
                "(start one with nami_begin_session first)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file to edit (e.g. '/workspace/project/nami_ai/OllamaTools/example.py').",
                    },
                    "old_str": {
                        "type": "string",
                        "description": "Exact string to find and replace. Must be unique in the file.",
                    },
                    "new_str": {
                        "type": "string",
                        "description": "Replacement string.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Human-readable description of the change (for audit trail).",
                    },
                    "auto_reload": {
                        "type": "boolean",
                        "description": "Emit system.reload_tools event after successful edit (default: true).",
                    },
                },
                "required": ["file_path", "old_str", "new_str"],
            },
        },
        "func": nami_edit_code,
    }]
