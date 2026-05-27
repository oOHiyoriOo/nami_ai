"""
Tests for OllamaTools/ tool loading.

Covers:
- Every .py file in OllamaTools/ that exports get_tool() can be imported
- get_tool() returns a dict with required keys: 'type', 'function', 'func'
- 'function' dict has 'name', 'description', 'parameters'
- 'func' is an async callable
- No two tools share the same function name (no collision)
- Tool schemas have valid parameter types (object)
"""

import asyncio
import importlib.util
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

TOOLS_DIR = Path(__file__).parent.parent / "OllamaTools"


def _load_tool_modules():
    """Import all .py files in OllamaTools/ that have a get_tool() function."""
    modules = {}
    for f in sorted(TOOLS_DIR.glob("*.py")):
        if f.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f.stem, f)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            modules[f.stem] = {"error": str(e)}
            continue
        if hasattr(mod, "get_tool"):
            modules[f.stem] = mod
    return modules


def test_all_tools_importable():
    """All tool modules import without errors."""
    print("Test: OllamaTools — all modules importable")
    failed = []
    for f in sorted(TOOLS_DIR.glob("*.py")):
        if f.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f.stem, f)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            failed.append(f"{f.name}: {e}")
    if failed:
        print(f"  [FAIL] import errors:\n  " + "\n  ".join(failed))
        return False
    print("  [PASS]")
    return True


def _iter_tools(mod):
    """
    Yield individual tool dicts from a module's get_tool() result.
    Handles both a single dict return and a list of dicts.
    """
    result = mod.get_tool()
    if isinstance(result, list):
        yield from result
    else:
        yield result


def test_get_tool_returns_dict():
    """Every tool with get_tool() returns a dict or list of dicts."""
    print("Test: OllamaTools — get_tool() returns dict or list of dicts")
    modules = _load_tool_modules()
    failed = []
    for name, mod in modules.items():
        if isinstance(mod, dict):  # import error
            continue
        try:
            result = mod.get_tool()
            if not isinstance(result, (dict, list)):
                failed.append(f"{name}: returned {type(result).__name__}")
            elif isinstance(result, list) and not all(isinstance(t, dict) for t in result):
                failed.append(f"{name}: list contains non-dict items")
        except Exception as e:
            failed.append(f"{name}: {e}")
    if failed:
        print(f"  [FAIL]\n  " + "\n  ".join(failed))
        return False
    print("  [PASS]")
    return True


def test_tool_has_required_keys():
    """Each tool dict has 'type', 'function', and 'func'."""
    print("Test: OllamaTools — required keys present in tool dict")
    modules = _load_tool_modules()
    failed = []
    for name, mod in modules.items():
        if isinstance(mod, dict):
            continue
        for tool in _iter_tools(mod):
            for key in ("type", "function", "func"):
                if key not in tool:
                    failed.append(f"{name}: missing key '{key}'")
    if failed:
        print(f"  [FAIL]\n  " + "\n  ".join(failed))
        return False
    print("  [PASS]")
    return True


def test_function_schema_valid():
    """'function' dict has 'name', 'description', and 'parameters'."""
    print("Test: OllamaTools — function schema has name, description, parameters")
    modules = _load_tool_modules()
    failed = []
    for name, mod in modules.items():
        if isinstance(mod, dict):
            continue
        for tool in _iter_tools(mod):
            fn = tool.get("function", {})
            for key in ("name", "description", "parameters"):
                if key not in fn:
                    failed.append(f"{name}: function missing '{key}'")
            params = fn.get("parameters", {})
            if params.get("type") != "object":
                failed.append(f"{name}: parameters.type must be 'object', got {params.get('type')!r}")
    if failed:
        print(f"  [FAIL]\n  " + "\n  ".join(failed))
        return False
    print("  [PASS]")
    return True


def test_func_is_async_callable():
    """'func' in each tool is an async callable."""
    print("Test: OllamaTools — func is async callable")
    modules = _load_tool_modules()
    failed = []
    for name, mod in modules.items():
        if isinstance(mod, dict):
            continue
        for tool in _iter_tools(mod):
            fn = tool.get("func")
            tool_name = tool.get("function", {}).get("name", name)
            if fn is None:
                failed.append(f"{tool_name}: func is None")
            elif not callable(fn):
                failed.append(f"{tool_name}: func is not callable")
            elif not inspect.iscoroutinefunction(fn):
                failed.append(f"{tool_name}: func is not async (use 'async def')")
    if failed:
        print(f"  [FAIL]\n  " + "\n  ".join(failed))
        return False
    print("  [PASS]")
    return True


def test_no_name_collisions():
    """No two tools share the same function name."""
    print("Test: OllamaTools — no function name collisions")
    modules = _load_tool_modules()
    seen_names = {}
    collisions = []
    for mod_name, mod in modules.items():
        if isinstance(mod, dict):
            continue
        for tool in _iter_tools(mod):
            fn_name = tool.get("function", {}).get("name")
            if fn_name in seen_names:
                collisions.append(f"'{fn_name}' defined in both {seen_names[fn_name]} and {mod_name}")
            else:
                seen_names[fn_name] = mod_name
    if collisions:
        print(f"  [FAIL]\n  " + "\n  ".join(collisions))
        return False
    print(f"  [PASS] ({len(seen_names)} tools: {', '.join(sorted(seen_names))})")
    return True


def test_tool_type_is_function():
    """Each tool dict has type='function'."""
    print("Test: OllamaTools — type field is 'function'")
    modules = _load_tool_modules()
    failed = []
    for name, mod in modules.items():
        if isinstance(mod, dict):
            continue
        for tool in _iter_tools(mod):
            if tool.get("type") != "function":
                failed.append(f"{name}: type={tool.get('type')!r} (expected 'function')")
    if failed:
        print(f"  [FAIL]\n  " + "\n  ".join(failed))
        return False
    print("  [PASS]")
    return True


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))