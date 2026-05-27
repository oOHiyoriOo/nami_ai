"""
Tests for MemoryService.format_memories edge cases.

Covers:
- Empty list → returns None
- All entries below similarity_threshold → returns None
- Mixed vector/context entries with varying scores
- Entry with missing 'text' key → skipped
- Only context entries → all included regardless of score
- Vector entry at exactly similarity_threshold → included (>= check)
- Formatted output format verification
- All entries with text=None or empty → returns None
- Context entry with score=0.0 → still included
"""

import importlib.util
import sys
import types
from pathlib import Path


def _load_module():
    """Load memory_service module directly, bypassing lib.services.__init__ cascade.

    memory_service.py imports MemoryHierarchy and MemoryDecayService from sibling
    modules. We pre-populate sys.modules with stub modules so those imports resolve
    without triggering the full lib.services.__init__ chain.
    """
    _saved_lib_services = sys.modules.get("lib.services")
    _saved_memory_hierarchy = sys.modules.get("lib.services.memory_hierarchy")
    _saved_memory_decay = sys.modules.get("lib.services.memory_decay")

    try:
        svc_pkg = types.ModuleType("lib.services")
        sys.modules["lib.services"] = svc_pkg

        for name, cls_name in [
            ("lib.services.memory_hierarchy", "MemoryHierarchy"),
            ("lib.services.memory_decay", "MemoryDecayService"),
        ]:
            mod = types.ModuleType(name)
            setattr(mod, cls_name, type(cls_name, (), {}))
            sys.modules[name] = mod

        filepath = Path(__file__).parent.parent / "lib" / "services" / "memory_service.py"
        spec = importlib.util.spec_from_file_location("memory_service", filepath)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for key, saved in [
            ("lib.services", _saved_lib_services),
            ("lib.services.memory_hierarchy", _saved_memory_hierarchy),
            ("lib.services.memory_decay", _saved_memory_decay),
        ]:
            if saved is not None:
                sys.modules[key] = saved
            else:
                sys.modules.pop(key, None)


_ms = _load_module()
MemoryService = _ms.MemoryService

THRESHOLD = 0.65


def _make_service(threshold: float = THRESHOLD) -> MemoryService:
    return MemoryService(None, similarity_threshold=threshold, enable_hierarchy=False, enable_decay=False)


# ============================================================
# Empty / all-below-threshold → None
# ============================================================

def test_empty_list_returns_none():
    svc = _make_service()
    assert svc.format_memories([]) is None


def test_all_vector_below_threshold_returns_none():
    svc = _make_service()
    memories = [
        {"text": "memory one", "type": "vector", "score": 0.10},
        {"text": "memory two", "type": "vector", "score": 0.30},
        {"text": "memory three", "type": "vector", "score": 0.64},
    ]
    assert svc.format_memories(memories) is None


# ============================================================
# Mixed vector/context entries
# ============================================================

def test_mixed_entries_varying_scores():
    svc = _make_service()
    memories = [
        {"text": "below threshold", "type": "vector", "score": 0.10},
        {"text": "context always included", "type": "context", "score": 0.0},
        {"text": "above threshold", "type": "vector", "score": 0.80},
        {"text": "another context", "type": "context", "score": 1.0},
        {"text": "also below", "type": "vector", "score": 0.50},
    ]
    result = svc.format_memories(memories)
    assert result is not None
    lines = result.split("\n")
    assert lines[0] == "Relevant memories:"
    assert "- context always included (Context)" in lines
    assert "- above threshold (Score: 0.80)" in lines
    assert "- another context (Context)" in lines
    assert "- below threshold" not in result
    assert "- also below" not in result


# ============================================================
# Missing 'text' key → skipped
# ============================================================

def test_entry_missing_text_key_skipped():
    svc = _make_service()
    memories = [
        {"type": "vector", "score": 0.90},  # no 'text' key
        {"text": "has text", "type": "vector", "score": 0.90},
    ]
    result = svc.format_memories(memories)
    assert result is not None
    assert "has text" in result
    # Only one entry should appear (the one with text)
    assert result.count("\n- ") == 1


# ============================================================
# Only context entries → all included regardless of score
# ============================================================

def test_only_context_all_included():
    svc = _make_service()
    memories = [
        {"text": "ctx one", "type": "context", "score": 0.0},
        {"text": "ctx two", "type": "context", "score": 0.001},
        {"text": "ctx three", "type": "context", "score": -999.0},
    ]
    result = svc.format_memories(memories)
    assert result is not None
    assert "- ctx one (Context)" in result
    assert "- ctx two (Context)" in result
    assert "- ctx three (Context)" in result
    assert result.count("(Context)") == 3


# ============================================================
# Vector entry at exactly similarity_threshold → included
# ============================================================

def test_vector_at_exact_threshold_included():
    svc = _make_service(threshold=0.65)
    memories = [
        {"text": "exactly at threshold", "type": "vector", "score": 0.65},
        {"text": "just above", "type": "vector", "score": 0.6500001},
    ]
    result = svc.format_memories(memories)
    assert result is not None
    assert "exactly at threshold (Score: 0.65)" in result
    assert "just above" in result


def test_vector_at_exact_threshold_custom():
    svc = _make_service(threshold=0.42)
    memories = [
        {"text": "custom threshold", "type": "vector", "score": 0.42},
    ]
    result = svc.format_memories(memories)
    assert result is not None
    assert "custom threshold (Score: 0.42)" in result


# ============================================================
# Output format verification
# ============================================================

def test_output_format_vector_entry():
    svc = _make_service()
    memories = [
        {"text": "test memory", "type": "vector", "score": 0.75},
    ]
    result = svc.format_memories(memories)
    assert result == "Relevant memories:\n- test memory (Score: 0.75)"


def test_output_format_context_entry():
    svc = _make_service()
    memories = [
        {"text": "context memory", "type": "context", "score": 0.99},
    ]
    result = svc.format_memories(memories)
    assert result == "Relevant memories:\n- context memory (Context)"


def test_output_format_score_precision():
    """Score format uses two decimal places."""
    svc = _make_service()
    memories = [
        {"text": "precise", "type": "vector", "score": 0.876543},
    ]
    result = svc.format_memories(memories)
    assert result is not None
    assert "Score: 0.88" in result
    assert "Score: 0.876543" not in result


# ============================================================
# Edge: all entries text=None or empty → returns None
# ============================================================

def test_all_entries_text_none_returns_none():
    svc = _make_service()
    memories = [
        {"text": None, "type": "vector", "score": 0.90},
        {"text": None, "type": "context", "score": 0.50},
    ]
    assert svc.format_memories(memories) is None


def test_all_entries_text_empty_returns_none():
    svc = _make_service()
    memories = [
        {"text": "", "type": "vector", "score": 0.90},
        {"text": "", "type": "context", "score": 0.50},
    ]
    assert svc.format_memories(memories) is None


# ============================================================
# Edge: context entry with score=0.0 → still included
# ============================================================

def test_context_entry_score_zero_included():
    svc = _make_service()
    memories = [
        {"text": "zero score context", "type": "context", "score": 0.0},
    ]
    result = svc.format_memories(memories)
    assert result is not None
    assert "zero score context (Context)" in result


# ============================================================
# Runner
# ============================================================

if __name__ == "__main__":
    import pytest
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
