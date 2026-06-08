"""
memory_processor.py — Shared memory extraction and direct storage.

Extracts memories from a completed conversation turn (user message +
assistant response) and writes them directly to Neo4j — no queue,
no batch worker. Memory writes are infrequent enough (~1 per turn)
that Neo4j handles them without any buffering.

Extraction thresholds prevent firing the extraction AI call on low-value
turns (e.g. "ok", "lol"):
  - Minimum content length (chars) — configurable via memory.extraction_min_chars
  - Per-conversation cooldown — configurable via memory.extraction_cooldown_seconds
  - Tool-call override: turns with tool calls always extract (high-signal content)

Used by both the REST API (via FastAPI BackgroundTasks) and AdapterManager.
"""

import logging
import time
from datetime import datetime, timezone

from lib.global_registry import g_data
from lib.memory_db import MemoryDb

# In-memory cooldown tracker: conversation_id → last extraction timestamp (epoch seconds).
# Ephemeral — resets on restart, which just means one extra extraction on cold start.
# Periodically pruned to prevent unbounded growth under long uptime.
_last_extraction: dict[str, float] = {}
_MAX_COOLDOWN_ENTRIES = 1000

_DEFAULT_MIN_CHARS = 150
_DEFAULT_COOLDOWN_SECONDS = 180


def _prune_cooldowns(now: float, max_age: float) -> None:
    """Evict stale entries and enforce a max-size cap to prevent unbounded growth."""
    # Age-based: remove entries older than max_age
    stale = [k for k, v in _last_extraction.items() if now - v > max_age]
    for k in stale:
        del _last_extraction[k]
    # Size cap: if still over limit, evict oldest entries first (FIFO)
    excess = len(_last_extraction) - _MAX_COOLDOWN_ENTRIES
    if excess > 0:
        oldest = sorted(_last_extraction.items(), key=lambda x: x[1])[:excess]
        for k, _ in oldest:
            del _last_extraction[k]


def _should_extract(
    message_content: str,
    conversation_id: str,
    has_tool_calls: bool,
    cfg,
) -> bool:
    """Return True if extraction should proceed for this turn.

    Reads min_chars and cooldown_seconds from cfg.memory section with
    sensible defaults.  Tool-call turns always pass.

    Side effects: updates the in-memory cooldown tracker and prunes stale
    entries.
    """
    mem_cfg = cfg.data.get("memory", {}) if cfg else {}
    min_chars = mem_cfg.get("extraction_min_chars", _DEFAULT_MIN_CHARS)
    cooldown_seconds = mem_cfg.get("extraction_cooldown_seconds", _DEFAULT_COOLDOWN_SECONDS)

    now = time.time()

    # Tool-call turns always extract; they carry high-signal content.
    if not has_tool_calls:
        if len(message_content) < min_chars:
            logging.debug(
                f"[memory_processor] Skipping extraction — content too short "
                f"({len(message_content)} < {min_chars} chars)"
            )
            return False

        last = _last_extraction.get(conversation_id, 0.0)
        if (now - last) < cooldown_seconds:
            logging.debug(
                f"[memory_processor] Skipping extraction — cooldown active "
                f"({int(now - last)}s < {cooldown_seconds}s) for {conversation_id}"
            )
            return False

    _last_extraction[conversation_id] = now
    _prune_cooldowns(now, cooldown_seconds * 2)
    return True


async def _store_extracted_memories(
    extracted: list,
    memory_db,
    user_id: str,
    user_name: str,
) -> int:
    """Validate, deduplicate, and persist extracted memories to Neo4j.

    Returns the count of memories that were actually stored.
    """
    from lib.utils import slugify

    stored = 0
    for memory in extracted:
        if not memory.is_valid():
            continue
        if not _has_required_fields(memory):
            logging.debug(f"[memory_processor] Skipping {memory.memory_type} — missing required fields: {memory.memory_args}")
            continue
        if await _is_duplicate(memory_db, user_id, memory):
            logging.debug(f"[memory_processor] Skipping duplicate: {memory.memory_type}")
            continue

        # Store locations and determine primary location_id
        # Build a content string for association heuristic: link memory to location
        # only if the location name appears in the memory's text content.
        content_parts = []
        for v in memory.memory_args.values():
            if isinstance(v, str):
                content_parts.append(v)
        memory_text = ' '.join(content_parts).lower()

        location_id = None
        if getattr(memory, 'locations', None):
            for loc_data in memory.locations:
                # Handle both dict format (new) and string format (backward compat)
                if isinstance(loc_data, dict):
                    loc_name = (loc_data.get('name') or '').strip()
                    loc_desc = loc_data.get('description') or ''
                elif isinstance(loc_data, str):
                    loc_name = loc_data.strip()
                    loc_desc = ''
                else:
                    continue

                if not loc_name:
                    continue

                loc_id = slugify(loc_name)
                try:
                    await memory_db.add_location(
                        location_id=loc_id, name=loc_name, description=loc_desc
                    )
                except Exception as e:
                    logging.warning(f"[memory_processor] Failed to store location '{loc_name}': {e}")

                # Association heuristic: only link if location name appears in memory content
                if location_id is None and loc_name.lower() in memory_text:
                    location_id = loc_id

        await memory_db.add_memory(
            user_id=user_id,
            user_name=user_name,
            memory_type=memory.memory_type,
            memory_args=memory.memory_args,
            location_id=location_id,
        )
        stored += 1

    if stored:
        logging.info(f"[memory_processor] Stored {stored} memories for {user_name}")

    return stored


async def _publish_extraction_event(
    stored_count: int,
    user_id: str,
    user_name: str,
    conversation_id: str,
    event_bus,
) -> None:
    """Publish a memory.extracted event if at least one memory was stored."""
    if stored_count > 0 and event_bus:
        from lib.services.event_bus import Event
        await event_bus.publish(Event(
            type="memory.extracted",
            data={
                "stored": stored_count,
                "user_id": user_id,
                "user_name": user_name,
                "conversation_id": conversation_id,
            },
        ))


async def process_memories(
    message_content: str,
    user_id: str,
    user_name: str,
    conversation_id: str,
    timestamp: datetime | None = None,
    has_tool_calls: bool = False,
) -> None:
    """
    Extract memories from a conversation turn and write them directly to Neo4j.

    Fire-and-forget safe — all exceptions are caught and logged.

    Args:
        message_content: Combined text to extract from (e.g. "User: ...\nAssistant: ...").
        user_id:         Stable identifier for the user (e.g. "discord:123").
        user_name:       Human-readable display name.
        conversation_id: Channel or conversation identifier.
        timestamp:       Message timestamp; defaults to now.
        has_tool_calls:  If True, always extract regardless of thresholds (tool turns = high signal).
    """

    try:
        memory_extractor = g_data.get("memory_extractor")
        memory_db = g_data.get("memory_db")

        if not memory_extractor or not memory_db:
            return

        cfg = g_data.get("cfg")

        if not _should_extract(message_content, conversation_id, has_tool_calls, cfg):
            return

        ts = timestamp or datetime.now(tz=timezone.utc)
        extracted = await memory_extractor.extract_memories(
            message_content=message_content,
            user_name=user_name,
            timestamp=ts,
        )

        stored = await _store_extracted_memories(extracted, memory_db, user_id, user_name)
        await _publish_extraction_event(
            stored, user_id, user_name, conversation_id,
            g_data.get("event_bus"),
        )

    except Exception as e:
        logging.error(f"[memory_processor] Memory processing error: {e}", exc_info=True)


async def _is_duplicate(memory_db, user_id: str, memory, threshold: float = 0.95) -> bool:
    """Check if a near-identical memory already exists in Neo4j."""
    try:
        text = _memory_text(memory)
        if not text:
            return False
        results = await memory_db.search(query=text, filter_user_id=user_id, top_k=3)
        return any(len(r) >= 2 and r[1] >= threshold for r in results)
    except Exception as e:
        logging.warning(f"Error checking for duplicate memory: {e}")
        return False


def _has_required_fields(memory) -> bool:
    """Validate that memory_args contains the required field for its type."""
    field = MemoryDb.get_text_field(memory.memory_type)
    if field is None:
        return False
    return bool(memory.memory_args.get(field))


def _memory_text(memory) -> str:
    """Extract the searchable text from an ExtractedMemory."""
    field = MemoryDb.get_text_field(memory.memory_type)
    if field:
        text = memory.memory_args.get(field, "")
        if text:
            return text
        # ProceduralUnit has a secondary name fallback
        if memory.memory_type == "ProceduralUnit":
            return memory.memory_args.get("name", "")
    return ""
