"""
Tests for MemoryDb._get_memory_text — text extraction and fallback logic.

Covers:
- Known memory type with populated text field → returns that text
- Known memory type with empty text field → falls back to first non-empty string
- Known memory type with all empty values → returns None
- Unknown memory type → goes straight to fallback
- memory_args with mixed types (int, None, empty strings) → finds first valid string
- Empty memory_args → returns None
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import lib.memory_db  # noqa: E402


def _make_memory_db():
    """Create a MemoryDb instance for testing.

    MemoryDb uses Neo4j + Ollama but performs no network I/O during __init__,
    so we can construct it directly without any mocks.
    """
    from lib.memory_db import MemoryDb
    return MemoryDb("bolt://localhost:7687", "neo4j", "test")


_db = _make_memory_db()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_known_type_populated_field_returns_text():
    """EpisodicMemory with summary='went hiking' returns 'went hiking'."""
    result = _db._get_memory_text("EpisodicMemory", {"summary": "went hiking", "detail": "up mount fuji"})
    assert result == "went hiking", f"expected 'went hiking', got {result!r}"


def test_known_type_empty_field_falls_back():
    """EpisodicMemory with empty summary field → falls back to first non-empty string."""
    result = _db._get_memory_text("EpisodicMemory", {"summary": "", "detail": "user ate ramen"})
    assert result == "user ate ramen", f"expected 'user ate ramen', got {result!r}"


def test_known_type_all_empty_returns_none():
    """EpisodicMemory with all empty values → returns None."""
    result = _db._get_memory_text("EpisodicMemory", {"summary": "", "detail": ""})
    assert result is None, f"expected None, got {result!r}"


def test_unknown_type_goes_straight_to_fallback():
    """memory_type not in MEMORY_TYPES → fallback finds first non-empty string."""
    result = _db._get_memory_text("SomeUnknownType", {"name": "Alice", "bio": ""})
    assert result == "Alice", f"expected 'Alice', got {result!r}"


def test_mixed_types_finds_first_valid_string():
    """memory_args with int, None, empty string, then valid string → returns valid string."""
    result = _db._get_memory_text("EpisodicMemory", {
        "summary": None,
        "count": 42,
        "note": "",
        "detail": "valid text here",
        "extra": "should not reach this",
    })
    assert result == "valid text here", f"expected 'valid text here', got {result!r}"


def test_empty_memory_args_returns_none():
    """Empty memory_args → returns None."""
    result = _db._get_memory_text("EpisodicMemory", {})
    assert result is None, f"expected None, got {result!r}"


def test_knowledge_unit_uses_statement_field():
    """KnowledgeUnit type uses 'statement' as the text field."""
    result = _db._get_memory_text("KnowledgeUnit", {
        "statement": "Python is dynamic",
        "summary": "should not use this",
    })
    assert result == "Python is dynamic", f"expected 'Python is dynamic', got {result!r}"


def test_procedural_unit_uses_description_field():
    """ProceduralUnit type uses 'description' as the text field."""
    result = _db._get_memory_text("ProceduralUnit", {
        "description": "git push origin main",
        "command": "git push",
    })
    assert result == "git push origin main", f"expected 'git push origin main', got {result!r}"


def test_whitespace_only_field_returned_as_is():
    """Text field with only whitespace is truthy → returned directly."""
    result = _db._get_memory_text("EpisodicMemory", {"summary": "   ", "detail": "cleanup complete"})
    assert result == "   ", f"expected '   ', got {result!r}"


def test_unknown_type_all_empty_returns_none():
    """Unknown type with all empty values → returns None."""
    result = _db._get_memory_text("FakeType", {"x": "", "y": None, "z": 0})
    assert result is None, f"expected None, got {result!r}"


def test_known_type_missing_text_field_falls_back():
    """Known type where text_field key is absent → falls back."""
    result = _db._get_memory_text("EpisodicMemory", {"detail": "still works"})
    assert result == "still works", f"expected 'still works', got {result!r}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
