"""
Tests for MemoryExtractor._parse_response — AI response JSON parsing.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.services.memory_extractor import MemoryExtractor, ExtractedMemory


# --- helpers ---

def _make_extractor() -> MemoryExtractor:
    """Create a minimal MemoryExtractor instance suitable for testing _parse_response."""
    return MemoryExtractor(
        provider_registry=None,
        memory_db=None,
        provider_name="test",
    )


# --- test helpers ---

def _mem(type_: str, args: dict | None = None, concepts: list | None = None) -> dict:
    """Helper to build a memory dict for constructing valid JSON input."""
    return {
        "memory_type": type_,
        "memory_args": args or {},
        "concepts": concepts or [],
    }


def _is_valid_response(result: list, expected_types: list[str]) -> bool:
    """Check result list matches expected memory_types and all items are ExtractedMemory."""
    if not isinstance(result, list):
        return False
    if len(result) != len(expected_types):
        return False
    for item, expected_type in zip(result, expected_types):
        if not isinstance(item, ExtractedMemory):
            return False
        if item.memory_type != expected_type:
            return False
    return True


# --- tests ---

def test_valid_json_list():
    """Valid JSON list of memory objects → returns list of valid ExtractedMemory."""
    extractor = _make_extractor()
    raw = json.dumps([
        _mem("EpisodicMemory", {"event": "test"}, ["event"]),
        _mem("KnowledgeUnit", {"fact": "sky is blue"}, ["science"]),
    ])
    result = extractor._parse_response(raw)
    assert _is_valid_response(result, ["EpisodicMemory", "KnowledgeUnit"]), \
        f"Expected EpisodicMemory+KnowledgeUnit, got {result}"


def test_think_blocks_stripped():
    """Response containing <think>...</think> blocks → blocks are stripped before parsing."""
    extractor = _make_extractor()
    raw = "<think>Hmm, let me look at this</think>\n" + json.dumps([
        _mem("EpisodicMemory", {"event": "hello"}, ["greeting"]),
    ])
    result = extractor._parse_response(raw)
    assert _is_valid_response(result, ["EpisodicMemory"]), \
        f"Expected EpisodicMemory, got {result}"


def test_json_fences_stripped():
    """Response wrapped in ```json ... ``` fences → fences are stripped."""
    extractor = _make_extractor()
    raw = "```json\n" + json.dumps([
        _mem("KnowledgeUnit", {"fact": "Earth is round"}, ["geography"]),
    ]) + "\n```"
    result = extractor._parse_response(raw)
    assert _is_valid_response(result, ["KnowledgeUnit"]), \
        f"Expected KnowledgeUnit, got {result}"


def test_think_and_fences_both_stripped():
    """Response with both think blocks and code fences → both stripped."""
    extractor = _make_extractor()
    raw = (
        "<think>Let me think about this</think>\n"
        "```json\n"
        + json.dumps([
            _mem("ProceduralUnit", {"steps": ["step1"]}, ["process"]),
        ]) +
        "\n```"
    )
    result = extractor._parse_response(raw)
    assert _is_valid_response(result, ["ProceduralUnit"]), \
        f"Expected ProceduralUnit, got {result}"


def test_multiline_string_preserved():
    """Multi-line string in JSON value → newlines preserved, not stripped."""
    extractor = _make_extractor()
    raw = json.dumps([
        _mem("EpisodicMemory", {"summary": "Line 1\nLine 2\nLine 3"}, ["test"]),
    ])
    result = extractor._parse_response(raw)
    assert _is_valid_response(result, ["EpisodicMemory"]), \
        f"Expected EpisodicMemory, got {result}"
    assert result[0].memory_args["summary"] == "Line 1\nLine 2\nLine 3", \
        f"Multi-line content was corrupted: {result[0].memory_args['summary']!r}"


def test_multiline_with_think_and_fences():
    """Multi-line JSON with think block and code fences → content preserved."""
    extractor = _make_extractor()
    inner = json.dumps([
        _mem("KnowledgeUnit", {"fact": "Line A\nLine B"}, ["multiline"]),
    ])
    raw = f"<think>analyzing</think>\n```json\n{inner}\n```"
    result = extractor._parse_response(raw)
    assert _is_valid_response(result, ["KnowledgeUnit"]), \
        f"Expected KnowledgeUnit, got {result}"
    assert result[0].memory_args["fact"] == "Line A\nLine B", \
        f"Multi-line content was corrupted: {result[0].memory_args['fact']!r}"


def test_non_list_json_returns_empty():
    """Non-list JSON response (e.g., dict) → returns empty list."""
    extractor = _make_extractor()
    raw = json.dumps({"not": "a list"})
    result = extractor._parse_response(raw)
    assert result == [], f"Expected empty list, got {result}"


def test_missing_memory_type_excluded():
    """Item with missing memory_type key → defaults to '' → excluded by is_valid()."""
    extractor = _make_extractor()
    raw = json.dumps([
        {"memory_args": {"event": "test"}, "concepts": ["event"]},
        _mem("EpisodicMemory", {"event": "valid"}, ["greeting"]),
    ])
    result = extractor._parse_response(raw)
    assert _is_valid_response(result, ["EpisodicMemory"]), \
        f"Expected only EpisodicMemory (missing type excluded), got {result}"


def test_mix_valid_and_invalid():
    """Mix of valid and invalid items → only valid ones returned."""
    extractor = _make_extractor()
    raw = json.dumps([
        _mem(""),                                   # invalid - empty type
        _mem("EpisodicMemory", {"event": "a"}, ["a"]),
        _mem("BadType"),                            # invalid - unknown type
        _mem("KnowledgeUnit", {"fact": "f"}, ["f"]),
    ])
    result = extractor._parse_response(raw)
    assert _is_valid_response(result, ["EpisodicMemory", "KnowledgeUnit"]), \
        f"Expected EpisodicMemory+KnowledgeUnit, got {result}"


def test_empty_response_raises_json_decode_error():
    """Empty response string → raises JSONDecodeError."""
    extractor = _make_extractor()
    try:
        extractor._parse_response("")
        assert False, "Expected JSONDecodeError for empty string"
    except json.JSONDecodeError:
        pass  # expected


# --- runner ---
