"""
Tests for OllamaTools/retrieve_tool_response.py

Covers:
- get_tool schema validation
- retrieve_tool_response with valid/invalid UUIDs
- retrieve_tool_response when ToolResponseLog is unavailable
"""

import os
import sys
import asyncio
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from OllamaTools.retrieve_tool_response import get_tool, retrieve_tool_response
from lib.services.tool_response_log import ToolResponseLog
from lib.global_registry import g_data


def test_get_tool_schema():
    """get_tool returns a valid function schema."""
    print("Test: get_tool schema")
    tools = get_tool()
    if len(tools) != 1:
        print(f"  [FAIL] expected 1 tool, got {len(tools)}")
        return False

    t = tools[0]
    checks = [
        (t.get("type") == "function", "type must be function"),
        (t.get("safe") is True, "safe must be True"),
        (t.get("categories") == ["memory_read"], "categories must be [memory_read]"),
        (t["function"]["name"] == "retrieve_tool_response", "name mismatch"),
        ("uuid" in t["function"]["parameters"]["properties"], "uuid param missing"),
        (t["function"]["parameters"]["required"] == ["uuid"], "required mismatch"),
        (callable(t.get("func")), "func not callable"),
    ]
    for ok, msg in checks:
        if not ok:
            print(f"  [FAIL] {msg}")
            return False
    print("  [PASS]")


def test_retrieve_valid_uuid():
    """retrieve_tool_response returns stored data for a valid UUID."""
    print("Test: retrieve_tool_response with valid UUID")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        async def run():
            log = ToolResponseLog(db_path)
            await log.initialize()

            # Wire into g_data
            g_data.register("tool_response_log", log)

            uuid_val = await log.store("run_bash", "ls -la output\nfile1.txt")

            result = await retrieve_tool_response(uuid_val)
            import json
            data = json.loads(result)
            if not data.get("success"):
                raise AssertionError(f"expected success: {result}")
            if data["data"]["tool_name"] != "run_bash":
                raise AssertionError(f"tool_name mismatch: {data['data']['tool_name']}")
            if data["data"]["response_text"] != "ls -la output\nfile1.txt":
                raise AssertionError(f"response_text mismatch")
            if data.get("uuid") != uuid_val:
                raise AssertionError(f"uuid field mismatch: {data.get('uuid')}")
        asyncio.run(run())
    finally:
        os.unlink(db_path)
    print("  [PASS]")


def test_retrieve_invalid_uuid():
    """retrieve_tool_response returns error for unknown UUID."""
    print("Test: retrieve_tool_response with invalid UUID")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        async def run():
            log = ToolResponseLog(db_path)
            await log.initialize()
            g_data.register("tool_response_log", log)

            result = await retrieve_tool_response("nonexistent_uuid")
            import json
            data = json.loads(result)
            if data.get("success") is not False:
                raise AssertionError(f"expected failure: {result}")
            if "No stored response found" not in data.get("error", ""):
                raise AssertionError(f"unexpected error: {data.get('error')}")
        asyncio.run(run())
    finally:
        os.unlink(db_path)
    print("  [PASS]")


def test_retrieve_no_log_available():
    """retrieve_tool_response errors when ToolResponseLog is not in g_data."""
    print("Test: retrieve_tool_response without log available")
    async def run():
        # Remove tool_response_log from g_data
        old = g_data.get("tool_response_log")
        if "tool_response_log" in g_data._registry:
            del g_data._registry["tool_response_log"]

        try:
            result = await retrieve_tool_response("any-uuid")
            import json
            data = json.loads(result)
            if data.get("success") is not False:
                raise AssertionError(f"expected failure: {result}")
            if "not available" not in data.get("error", ""):
                raise AssertionError(f"unexpected error: {data.get('error')}")
        finally:
            if old is not None:
                g_data.register("tool_response_log", old)
    asyncio.run(run())
    print("  [PASS]")


if __name__ == "__main__":
    tests = [
        test_get_tool_schema,
        test_retrieve_valid_uuid,
        test_retrieve_invalid_uuid,
        test_retrieve_no_log_available,
    ]
    passed = 0
    failed = 0
    for test in tests:
        ok = test()
        if ok is False:
            failed += 1
        elif ok is None:
            passed += 1
    print(f"\nResults: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
