"""
Tests for lib/neo4j_lib/__init__.py: get_valid_properties, is_valid_memory_type, is_valid_property.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.neo4j_lib import (
    get_valid_properties,
    is_valid_memory_type,
    is_valid_property,
)


# ---------------------------------------------------------------------------
# get_valid_properties()
# ---------------------------------------------------------------------------

def test_get_valid_properties_episodic_memory():
    props = get_valid_properties("EpisodicMemory")
    expected = {
        "id", "summary", "description", "summaryEmbeddingVector",
        "timestampOccurred_approx", "timeDescription", "emotionalValence",
        "confidenceScore", "vividnessScore", "emotionalIntensity",
        "source", "authorUserId", "creationTimestamp", "location", "concepts",
    }
    assert props == expected, f"EpisodicMemory properties mismatch: {props ^ expected}"
    return True


def test_get_valid_properties_knowledge_unit():
    props = get_valid_properties("KnowledgeUnit")
    expected = {
        "id", "statement", "summaryEmbeddingVector", "type",
        "confidenceScore", "source", "creationTimestamp", "validFrom",
        "validUntil", "authorUserId", "concepts",
    }
    assert props == expected, f"KnowledgeUnit properties mismatch: {props ^ expected}"
    return True


def test_get_valid_properties_procedural_unit():
    props = get_valid_properties("ProceduralUnit")
    expected = {
        "id", "name", "description", "steps", "summaryEmbeddingVector",
        "proficiencyLevel", "confidenceScore", "authorUserId",
        "creationTimestamp", "concepts",
    }
    assert props == expected, f"ProceduralUnit properties mismatch: {props ^ expected}"
    return True


def test_get_valid_properties_invalid_type_raises():
    try:
        get_valid_properties("NonExistentType")
        assert False, "Expected ValueError but no exception raised"
    except ValueError as e:
        assert "Invalid memory_type" in str(e)
        assert "NonExistentType" in str(e)
    return True


# ---------------------------------------------------------------------------
# is_valid_memory_type()
# ---------------------------------------------------------------------------

def test_is_valid_memory_type_known_types():
    for t in ("EpisodicMemory", "KnowledgeUnit", "ProceduralUnit"):
        assert is_valid_memory_type(t), f"Expected True for {t}"
    return True


def test_is_valid_memory_type_unknown_type():
    assert not is_valid_memory_type("FooBar")
    assert not is_valid_memory_type("")
    assert not is_valid_memory_type("episodicmemory")  # case-sensitive
    return True


# ---------------------------------------------------------------------------
# is_valid_property()
# ---------------------------------------------------------------------------

def test_is_valid_property_known_property():
    assert is_valid_property("EpisodicMemory", "summary")
    assert is_valid_property("EpisodicMemory", "emotionalValence")
    assert is_valid_property("KnowledgeUnit", "statement")
    assert is_valid_property("ProceduralUnit", "name")
    return True


def test_is_valid_property_common_property():
    common = (
        "lastModifiedTimestamp", "lastAccessedTimestamp", "access_count",
        "importance", "mergedFromCount", "decay_factor",
    )
    for prop in common:
        for mem_type in ("EpisodicMemory", "KnowledgeUnit", "ProceduralUnit"):
            assert is_valid_property(mem_type, prop), (
                f"Expected True for common property {prop} on {mem_type}"
            )
    return True


def test_is_valid_property_unknown_property():
    assert not is_valid_property("EpisodicMemory", "nonExistentProp")
    assert not is_valid_property("KnowledgeUnit", "bogusField")
    return True


def test_is_valid_property_invalid_type():
    assert not is_valid_property("FooBar", "summary")
    assert not is_valid_property("", "summary")
    return True


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))