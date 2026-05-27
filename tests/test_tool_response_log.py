"""
Tests for lib/services/tool_response_log.py

Covers:
- Placeholder creation and parsing
- Store / get / delete round-trip
- prune_old with retention window
- Multiple stores, concurrent access patterns
- get_count
- Integration: store via ToolResponseLog and verify placeholder format
"""

import os
import sys
import asyncio
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.services.tool_response_log import (
    ToolResponseLog,
    make_placeholder,
    parse_placeholder,
    PLACEHOLDER_PREFIX,
    PLACEHOLDER_SUFFIX,
)


def test_make_placeholder():
    """make_placeholder wraps a UUID in [TOOL_RESPONSE:...] format."""
    print("Test: make_placeholder format")
    uuid = "abc123def456"
    ph = make_placeholder(uuid)
    if ph != f"{PLACEHOLDER_PREFIX}abc123def456{PLACEHOLDER_SUFFIX}":
        print(f"  [FAIL] placeholder={ph!r}")
        return False
    print("  [PASS]")
    return True


def test_parse_placeholder():
    """parse_placeholder extracts UUID from valid placeholder, returns None otherwise."""
    print("Test: parse_placeholder")

    # Valid
    uuid = parse_placeholder(f"{PLACEHOLDER_PREFIX}my-uuid{PLACEHOLDER_SUFFIX}")
    if uuid != "my-uuid":
        print(f"  [FAIL] parsed={uuid!r}")
        return False

    # Invalid — missing prefix
    if parse_placeholder("no-prefix") is not None:
        print("  [FAIL] should return None for invalid")
        return False

    # Invalid — missing suffix
    if parse_placeholder(f"{PLACEHOLDER_PREFIX}no-suffix") is not None:
        print("  [FAIL] should return None for missing suffix")
        return False

    # Empty
    if parse_placeholder("") is not None:
        print("  [FAIL] should return None for empty")
        return False

    print("  [PASS]")
    return True


async def _store_and_get(db_path: str):
    """Helper: create log, store entry, retrieve, verify round-trip."""
    log = ToolResponseLog(db_path)
    await log.initialize()

    meta = {"tool_call_id": "call_42", "conversation_id": "ch_1"}
    uuid_val = await log.store("sandbox_read_file", "file contents here\nline2", meta)

    # Verify UUID format (32 hex chars)
    if len(uuid_val) != 32:
        raise AssertionError(f"UUID length {len(uuid_val)} (expected 32)")

    # Retrieve by UUID
    record = await log.get(uuid_val)
    if record is None:
        raise AssertionError("record not found")
    if record["tool_name"] != "sandbox_read_file":
        raise AssertionError(f"tool_name={record['tool_name']!r}")
    if record["response_text"] != "file contents here\nline2":
        raise AssertionError(f"response_text={record['response_text']!r}")
    if record["metadata"].get("tool_call_id") != "call_42":
        raise AssertionError(f"metadata.tool_call_id={record['metadata']!r}")

    # get() unknown UUID returns None
    if await log.get("nonexistent1234567890abcdef") is not None:
        raise AssertionError("get(nonexistent) should return None")

    return uuid_val, log


def test_store_and_get():
    """Round-trip: store → get returns the same data."""
    print("Test: store and get round-trip")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        asyncio.run(_store_and_get(db_path))
    finally:
        os.unlink(db_path)
    print("  [PASS]")
    return True


def test_delete():
    """delete removes a record; subsequent get returns None."""
    print("Test: delete")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        async def run():
            log = ToolResponseLog(db_path)
            await log.initialize()
            uuid_val = await log.store("tool_a", "response_text")
            # Verify it exists
            if await log.get(uuid_val) is None:
                raise AssertionError("should exist before delete")
            # Delete
            deleted = await log.delete(uuid_val)
            if not deleted:
                raise AssertionError("delete should return True")
            # Verify gone
            if await log.get(uuid_val) is not None:
                raise AssertionError("should be None after delete")
            # Delete non-existent
            if await log.delete("nonexistent"):
                raise AssertionError("delete(nonexistent) should return False")
        asyncio.run(run())
    finally:
        os.unlink(db_path)
    print("  [PASS]")
    return True


def test_get_count():
    """get_count returns the number of stored responses."""
    print("Test: get_count")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        async def run():
            log = ToolResponseLog(db_path)
            await log.initialize()
            if await log.get_count() != 0:
                raise AssertionError("count should start at 0")
            await log.store("t1", "r1")
            await log.store("t2", "r2")
            if await log.get_count() != 2:
                raise AssertionError(f"count should be 2, got {await log.get_count()}")
        asyncio.run(run())
    finally:
        os.unlink(db_path)
    print("  [PASS]")
    return True


def test_multiple_stores():
    """Store multiple responses with different tool names; all retrievable."""
    print("Test: multiple stores")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        async def run():
            log = ToolResponseLog(db_path)
            await log.initialize()
            uuids = []
            for i in range(5):
                uid = await log.store(f"tool_{i}", f"response_{i}")
                uuids.append(uid)
            if await log.get_count() != 5:
                raise AssertionError(f"count={await log.get_count()} (expected 5)")
            for i, uid in enumerate(uuids):
                record = await log.get(uid)
                if record["tool_name"] != f"tool_{i}":
                    raise AssertionError(f"tool_name mismatch at {i}")
                if record["response_text"] != f"response_{i}":
                    raise AssertionError(f"response_text mismatch at {i}")
            # All UUIDs unique
            if len(set(uuids)) != 5:
                raise AssertionError("UUIDs not unique")
        asyncio.run(run())
    finally:
        os.unlink(db_path)
    print("  [PASS]")
    return True


def test_prune_old():
    """prune_old deletes entries older than retention_days."""
    print("Test: prune_old")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        async def run():
            log = ToolResponseLog(db_path)
            await log.initialize()
            # Store a response with an old timestamp by direct SQL
            import aiosqlite
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    "INSERT INTO tool_response_log (uuid, timestamp, tool_name, response_text) "
                    "VALUES (?, datetime('now', '-60 days'), ?, ?)",
                    ("old_entry_01", "old_tool", "old response"),
                )
                await db.execute(
                    "INSERT INTO tool_response_log (uuid, timestamp, tool_name, response_text) "
                    "VALUES (?, datetime('now', '-1 days'), ?, ?)",
                    ("recent_entry_01", "recent_tool", "recent response"),
                )
                await db.commit()

            # Prune entries older than 30 days — only the old one should go
            deleted = await log.prune_old(retention_days=30)
            if deleted != 1:
                raise AssertionError(f"prune_old should delete 1, got {deleted}")
            if await log.get_count() != 1:
                raise AssertionError(f"count should be 1 after prune")
            if await log.get("old_entry_01") is not None:
                raise AssertionError("old entry should be deleted")
            if await log.get("recent_entry_01") is None:
                raise AssertionError("recent entry should remain")

            # Prune with 0 days — deletes everything
            deleted2 = await log.prune_old(retention_days=0)
            if deleted2 != 1:
                raise AssertionError(f"prune_old(0) should delete 1, got {deleted2}")
            if await log.get_count() != 0:
                raise AssertionError("count should be 0 after full prune")
        asyncio.run(run())
    finally:
        os.unlink(db_path)
    print("  [PASS]")
    return True


def test_placeholder_roundtrip():
    """Placeholder created from stored UUID can be parsed back."""
    print("Test: placeholder roundtrip")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        async def run():
            log = ToolResponseLog(db_path)
            await log.initialize()
            uuid_val = await log.store("run_bash", "ls output")
            ph = make_placeholder(uuid_val)
            parsed = parse_placeholder(ph)
            if parsed != uuid_val:
                raise AssertionError(f"parsed={parsed!r} != {uuid_val!r}")
            record = await log.get(parsed)
            if record is None or record["response_text"] != "ls output":
                raise AssertionError("roundtrip lookup failed")
        asyncio.run(run())
    finally:
        os.unlink(db_path)
    print("  [PASS]")
    return True


def test_large_response():
    """Responses up to 100KB are stored and retrieved correctly."""
    print("Test: large response (100KB)")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        async def run():
            log = ToolResponseLog(db_path)
            await log.initialize()
            large_text = "x" * 100_000
            uuid_val = await log.store("sandbox_read_file", large_text)
            record = await log.get(uuid_val)
            if len(record["response_text"]) != 100_000:
                raise AssertionError(f"length={len(record['response_text'])}")
            if record["response_text"] != large_text:
                raise AssertionError("content mismatch")
        asyncio.run(run())
    finally:
        os.unlink(db_path)
    print("  [PASS]")
    return True


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))