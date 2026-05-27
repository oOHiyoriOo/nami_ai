"""
Tests for Neo4j entity serialization contract (to_dict, get_label, get_properties, list_to_dicts).

Tests Concept, Person, and Location for the shared interface pattern.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.neo4j_lib.concept import Concept
from lib.neo4j_lib.episodic_memory import EpisodicMemory
from lib.neo4j_lib.knowledge_unit import KnowledgeUnit
from lib.neo4j_lib.location import Location
from lib.neo4j_lib.person import Person
from lib.neo4j_lib.procedural_unit import ProceduralUnit


# ---------------------------------------------------------------------------
# to_dict() — serializable types only
# ---------------------------------------------------------------------------

def test_concept_to_dict_serializable_only():
    """to_dict() returns only str, int, float, bool, list, dict, or None values."""
    c = Concept(id="c1", name="TestConcept", description="A concept")
    d = c.to_dict()
    allowed = (str, int, float, bool, list, dict, type(None))
    for k, v in d.items():
        assert isinstance(v, allowed), f"Key '{k}' has value of type {type(v)} — not serializable"
    return True


def test_person_to_dict_serializable_only():
    """Person.to_dict() returns only JSON-serializable types."""
    p = Person(id=1, name="Alice", nickname=None)
    d = p.to_dict()
    allowed = (str, int, float, bool, list, dict, type(None))
    for k, v in d.items():
        assert isinstance(v, allowed), f"Key '{k}' has value of type {type(v)} — not serializable"
    return True


def test_location_to_dict_serializable_only():
    """Location.to_dict() returns only JSON-serializable types."""
    loc = Location(id="loc1", name="TestLocation", description="A place")
    d = loc.to_dict()
    allowed = (str, int, float, bool, list, dict, type(None))
    for k, v in d.items():
        assert isinstance(v, allowed), f"Key '{k}' has value of type {type(v)} — not serializable"
    return True


# ---------------------------------------------------------------------------
# __iter__() — iterable as key-value pairs
# ---------------------------------------------------------------------------

def test_concept_iter_via_dict():
    """dict(Concept(...)) produces the same result as to_dict()."""
    c = Concept(id="c2", name="Tag", keywords=["ai", "ml"])
    assert dict(c) == c.to_dict(), "dict(entity) must match to_dict()"
    return True


def test_person_iter_via_dict():
    """dict(Person(...)) produces the same result as to_dict()."""
    p = Person(id=2, name="Bob", nickname=None)
    assert dict(p) == p.to_dict(), "dict(entity) must match to_dict()"
    return True


def test_location_iter_via_dict():
    """dict(Location(...)) produces the same result as to_dict()."""
    loc = Location(id="loc2", name="Place", planeOfExistence="Material")
    assert dict(loc) == loc.to_dict(), "dict(entity) must match to_dict()"
    return True


# ---------------------------------------------------------------------------
# __json__() — valid JSON
# ---------------------------------------------------------------------------

def test_concept_json_valid():
    """Concept.__json__() returns valid JSON parseable back to a dict."""
    c = Concept(id="c3", name="JSONable", description="test")
    raw = c.__json__()
    parsed = json.loads(raw)
    assert isinstance(parsed, dict), f"Expected dict from JSON, got {type(parsed)}"
    assert parsed == c.to_dict(), "JSON round-trip must match to_dict()"
    return True


def test_person_json_valid():
    """Person.__json__() returns valid JSON parseable back to a dict."""
    p = Person(id=99, name="JsonPerson", nickname="JP")
    raw = p.__json__()
    parsed = json.loads(raw)
    assert isinstance(parsed, dict), f"Expected dict from JSON, got {type(parsed)}"
    assert parsed == p.to_dict(), "JSON round-trip must match to_dict()"
    return True


def test_location_json_valid():
    """Location.__json__() returns valid JSON parseable back to a dict."""
    loc = Location(id="loc3", name="JSONable", description="test")
    raw = loc.__json__()
    parsed = json.loads(raw)
    assert isinstance(parsed, dict), f"Expected dict from JSON, got {type(parsed)}"
    assert parsed == loc.to_dict(), "JSON round-trip must match to_dict()"
    return True


# ---------------------------------------------------------------------------
# get_label() — correct label string
# ---------------------------------------------------------------------------

def test_concept_get_label():
    """Concept.get_label() returns 'CONCEPT'."""
    c = Concept(id="l1", name="LabelTest")
    assert c.get_label() == "CONCEPT", f"Expected 'CONCEPT', got '{c.get_label()}'"
    return True


def test_person_get_label():
    """Person.get_label() returns 'Person'."""
    p = Person(id=1, name="LabelPerson")
    assert p.get_label() == "Person", f"Expected 'Person', got '{p.get_label()}'"
    return True


def test_location_get_label():
    """Location.get_label() returns 'Location'."""
    loc = Location(id="l1", name="LabelTest")
    assert loc.get_label() == "Location", f"Expected 'Location', got '{loc.get_label()}'"
    return True


# ---------------------------------------------------------------------------
# get_properties() — excludes None, includes falsy-not-None
# ---------------------------------------------------------------------------

def test_concept_get_properties_excludes_none():
    """get_properties() excludes keys whose value is None."""
    c = Concept(id="p1", name="Props", description=None)
    props = c.get_properties()
    assert "description" not in props, "description=None should be excluded"
    assert "id" in props
    assert "name" in props
    return True


def test_person_get_properties_excludes_none():
    """Person.get_properties() excludes None-valued keys."""
    p = Person(id=1, name="Props", nickname=None)
    props = p.get_properties()
    assert "nickname" not in props, "nickname=None should be excluded"
    assert "id" in props
    assert "name" in props
    return True


def test_concept_get_properties_includes_falsy():
    """get_properties() includes empty string, 0, False, empty list."""
    c = Concept(id="", name="", description="", keywords=[])
    props = c.get_properties()
    assert "id" in props, "Empty string 'id' should be present"
    assert "name" in props, "Empty string 'name' should be present"
    assert "description" in props, "Empty string 'description' should be present"
    assert "keywords" in props, "Empty list 'keywords' should be present"
    assert props["id"] == ""
    assert props["keywords"] == []
    return True


def test_person_get_properties_includes_falsy():
    """Person.get_properties() includes 0 and empty string."""
    p = Person(id=0, name="", nickname="")
    props = p.get_properties()
    assert "id" in props, "0-valued 'id' should be present"
    assert "name" in props, "Empty string 'name' should be present"
    assert "nickname" in props, "Empty string 'nickname' should be present"
    assert props["id"] == 0
    assert props["name"] == ""
    return True


def test_location_get_properties_excludes_none():
    """Location.get_properties() excludes None-valued keys."""
    loc = Location(id="p1", name="Props", description=None, planeOfExistence=None)
    props = loc.get_properties()
    assert "description" not in props, "description=None should be excluded"
    assert "planeOfExistence" not in props, "planeOfExistence=None should be excluded"
    assert "id" in props
    assert "name" in props
    return True


def test_location_get_properties_includes_falsy():
    """Location.get_properties() includes empty string values."""
    loc = Location(id="", name="", description="", planeOfExistence="")
    props = loc.get_properties()
    assert "id" in props, "Empty string 'id' should be present"
    assert "name" in props, "Empty string 'name' should be present"
    assert "description" in props, "Empty string 'description' should be present"
    assert "planeOfExistence" in props, "Empty string 'planeOfExistence' should be present"
    assert props["id"] == ""
    assert props["name"] == ""
    return True


# ---------------------------------------------------------------------------
# list_to_dicts() — entity list and mixed list
# ---------------------------------------------------------------------------

def test_concept_list_to_dicts():
    """list_to_dicts() converts a list of entity objects."""
    concepts = [
        Concept(id="x1", name="Alpha"),
        Concept(id="x2", name="Beta", description="second"),
    ]
    result = Concept.list_to_dicts(concepts)
    assert len(result) == 2
    assert all(isinstance(d, dict) for d in result)
    assert result[0]["id"] == "x1"
    assert result[1]["id"] == "x2"
    return True


def test_person_list_to_dicts():
    """Person.list_to_dicts() converts a list of entity objects."""
    persons = [
        Person(id=10, name="Carol"),
        Person(id=20, name="Dave", nickname="D"),
    ]
    result = Person.list_to_dicts(persons)
    assert len(result) == 2
    assert all(isinstance(d, dict) for d in result)
    assert result[0]["id"] == 10
    assert result[1]["name"] == "Dave"
    return True


def test_list_to_dicts_mixed_entities_and_dicts():
    """list_to_dicts() handles a list with mixed entities and plain dicts."""
    items = [
        Concept(id="m1", name="Mixed1"),
        {"id": "plain", "name": "PlainDict"},
        Concept(id="m2", name="Mixed2"),
    ]
    result = Concept.list_to_dicts(items)
    assert len(result) == 3
    assert result[0] == {"id": "m1", "name": "Mixed1", "description": None, "keywords": []}
    assert result[1] == {"id": "plain", "name": "PlainDict"}  # plain dict passes through
    assert result[2]["id"] == "m2"
    return True


def test_location_list_to_dicts():
    """Location.list_to_dicts() converts a list of entity objects."""
    locations = [
        Location(id="x1", name="Alpha"),
        Location(id="x2", name="Beta", description="second"),
    ]
    result = Location.list_to_dicts(locations)
    assert len(result) == 2
    assert all(isinstance(d, dict) for d in result)
    assert result[0]["id"] == "x1"
    assert result[1]["id"] == "x2"
    return True


# ---------------------------------------------------------------------------
# EpisodicMemory
# ---------------------------------------------------------------------------

def test_episodic_memory_to_dict_serializable_only():
    """EpisodicMemory.to_dict() returns only JSON-serializable types."""
    em = EpisodicMemory(id="em1", summary="Test memory", emotionalValence=0.5, confidenceScore=3)
    d = em.to_dict()
    allowed = (str, int, float, bool, list, dict, type(None))
    for k, v in d.items():
        assert isinstance(v, allowed), f"Key '{k}' has value of type {type(v)} — not serializable"
    return True


def test_episodic_memory_iter_via_dict():
    """dict(EpisodicMemory(...)) produces the same result as to_dict()."""
    em = EpisodicMemory(id="em2", summary="Event", emotionalIntensity=7)
    assert dict(em) == em.to_dict(), "dict(entity) must match to_dict()"
    return True


def test_episodic_memory_json_valid():
    """EpisodicMemory.__json__() returns valid JSON parseable back to a dict."""
    em = EpisodicMemory(id="em3", summary="JSON event", description="test")
    raw = em.__json__()
    parsed = json.loads(raw)
    assert isinstance(parsed, dict), f"Expected dict from JSON, got {type(parsed)}"
    assert parsed == em.to_dict(), "JSON round-trip must match to_dict()"
    return True


def test_episodic_memory_get_label():
    """EpisodicMemory.get_label() returns 'EpisodicMemory'."""
    em = EpisodicMemory(id="l1", summary="LabelTest")
    assert em.get_label() == "EpisodicMemory", f"Expected 'EpisodicMemory', got '{em.get_label()}'"
    return True


def test_episodic_memory_get_properties_excludes_none():
    """EpisodicMemory.get_properties() excludes None-valued keys."""
    em = EpisodicMemory(id="p1", summary="Props", description=None, emotionalValence=None)
    props = em.get_properties()
    assert "description" not in props, "description=None should be excluded"
    assert "emotionalValence" not in props, "emotionalValence=None should be excluded"
    assert "id" in props
    assert "summary" in props
    return True


def test_episodic_memory_get_properties_includes_falsy():
    """EpisodicMemory.get_properties() includes 0, empty string, empty list."""
    em = EpisodicMemory(id="", summary="", emotionalIntensity=0, concepts=[])
    props = em.get_properties()
    assert "id" in props, "Empty string 'id' should be present"
    assert "summary" in props, "Empty string 'summary' should be present"
    assert "emotionalIntensity" in props, "0-valued 'emotionalIntensity' should be present"
    assert "concepts" in props, "Empty list 'concepts' should be present"
    assert props["id"] == ""
    assert props["emotionalIntensity"] == 0
    return True


def test_episodic_memory_list_to_dicts():
    """EpisodicMemory.list_to_dicts() converts a list of entity objects."""
    memories = [
        EpisodicMemory(id="x1", summary="First"),
        EpisodicMemory(id="x2", summary="Second", confidenceScore=5),
    ]
    result = EpisodicMemory.list_to_dicts(memories)
    assert len(result) == 2
    assert all(isinstance(d, dict) for d in result)
    assert result[0]["id"] == "x1"
    assert result[1]["id"] == "x2"
    return True


def test_episodic_memory_list_to_dicts_mixed():
    """EpisodicMemory.list_to_dicts() handles mixed entities and plain dicts."""
    items = [
        EpisodicMemory(id="m1", summary="Mixed1"),
        {"id": "plain", "summary": "PlainDict"},
        EpisodicMemory(id="m2", summary="Mixed2"),
    ]
    result = EpisodicMemory.list_to_dicts(items)
    assert len(result) == 3
    assert result[1] == {"id": "plain", "summary": "PlainDict"}
    assert result[2]["id"] == "m2"
    return True


def test_episodic_memory_str_repr():
    """EpisodicMemory.__str__() and __repr__() return non-empty strings."""
    em = EpisodicMemory(id="sr1", summary="StringTest")
    s = str(em)
    r = repr(em)
    assert isinstance(s, str) and len(s) > 0, "__str__() must return non-empty string"
    assert isinstance(r, str) and len(r) > 0, "__repr__() must return non-empty string"
    return True


# ---------------------------------------------------------------------------
# KnowledgeUnit
# ---------------------------------------------------------------------------

def test_knowledge_unit_to_dict_serializable_only():
    """KnowledgeUnit.to_dict() returns only JSON-serializable types."""
    ku = KnowledgeUnit(id="ku1", statement="Test fact", confidenceScore=0.9)
    d = ku.to_dict()
    allowed = (str, int, float, bool, list, dict, type(None))
    for k, v in d.items():
        assert isinstance(v, allowed), f"Key '{k}' has value of type {type(v)} — not serializable"
    return True


def test_knowledge_unit_iter_via_dict():
    """dict(KnowledgeUnit(...)) produces the same result as to_dict()."""
    ku = KnowledgeUnit(id="ku2", statement="A fact", type="assertion")
    assert dict(ku) == ku.to_dict(), "dict(entity) must match to_dict()"
    return True


def test_knowledge_unit_json_valid():
    """KnowledgeUnit.__json__() returns valid JSON parseable back to a dict."""
    ku = KnowledgeUnit(id="ku3", statement="JSON fact", source="test")
    raw = ku.__json__()
    parsed = json.loads(raw)
    assert isinstance(parsed, dict), f"Expected dict from JSON, got {type(parsed)}"
    assert parsed == ku.to_dict(), "JSON round-trip must match to_dict()"
    return True


def test_knowledge_unit_get_label():
    """KnowledgeUnit.get_label() returns 'KnowledgeUnit'."""
    ku = KnowledgeUnit(id="l1", statement="LabelTest")
    assert ku.get_label() == "KnowledgeUnit", f"Expected 'KnowledgeUnit', got '{ku.get_label()}'"
    return True


def test_knowledge_unit_get_properties_excludes_none():
    """KnowledgeUnit.get_properties() excludes None-valued keys."""
    ku = KnowledgeUnit(id="p1", statement="Props", type=None, confidenceScore=None)
    props = ku.get_properties()
    assert "type" not in props, "type=None should be excluded"
    assert "confidenceScore" not in props, "confidenceScore=None should be excluded"
    assert "id" in props
    assert "statement" in props
    return True


def test_knowledge_unit_get_properties_includes_falsy():
    """KnowledgeUnit.get_properties() includes 0, empty string, empty list."""
    ku = KnowledgeUnit(id="", statement="", confidenceScore=0, concepts=[])
    props = ku.get_properties()
    assert "id" in props, "Empty string 'id' should be present"
    assert "statement" in props, "Empty string 'statement' should be present"
    assert "confidenceScore" in props, "0-valued 'confidenceScore' should be present"
    assert "concepts" in props, "Empty list 'concepts' should be present"
    assert props["id"] == ""
    assert props["confidenceScore"] == 0
    return True


def test_knowledge_unit_list_to_dicts():
    """KnowledgeUnit.list_to_dicts() converts a list of entity objects."""
    units = [
        KnowledgeUnit(id="x1", statement="First"),
        KnowledgeUnit(id="x2", statement="Second", type="claim"),
    ]
    result = KnowledgeUnit.list_to_dicts(units)
    assert len(result) == 2
    assert all(isinstance(d, dict) for d in result)
    assert result[0]["id"] == "x1"
    assert result[1]["id"] == "x2"
    return True


def test_knowledge_unit_list_to_dicts_mixed():
    """KnowledgeUnit.list_to_dicts() handles mixed entities and plain dicts."""
    items = [
        KnowledgeUnit(id="m1", statement="Mixed1"),
        {"id": "plain", "statement": "PlainDict"},
        KnowledgeUnit(id="m2", statement="Mixed2"),
    ]
    result = KnowledgeUnit.list_to_dicts(items)
    assert len(result) == 3
    assert result[1] == {"id": "plain", "statement": "PlainDict"}
    assert result[2]["id"] == "m2"
    return True


def test_knowledge_unit_str_repr():
    """KnowledgeUnit.__str__() and __repr__() return non-empty strings."""
    ku = KnowledgeUnit(id="sr1", statement="StringTest")
    s = str(ku)
    r = repr(ku)
    assert isinstance(s, str) and len(s) > 0, "__str__() must return non-empty string"
    assert isinstance(r, str) and len(r) > 0, "__repr__() must return non-empty string"
    return True


# ---------------------------------------------------------------------------
# ProceduralUnit
# ---------------------------------------------------------------------------

def test_procedural_unit_to_dict_serializable_only():
    """ProceduralUnit.to_dict() returns only JSON-serializable types."""
    pu = ProceduralUnit(id="pu1", name="Test skill", proficiencyLevel=3, confidenceScore=0.8)
    d = pu.to_dict()
    allowed = (str, int, float, bool, list, dict, type(None))
    for k, v in d.items():
        assert isinstance(v, allowed), f"Key '{k}' has value of type {type(v)} — not serializable"
    return True


def test_procedural_unit_iter_via_dict():
    """dict(ProceduralUnit(...)) produces the same result as to_dict()."""
    pu = ProceduralUnit(id="pu2", name="A skill", steps="Step 1, Step 2")
    assert dict(pu) == pu.to_dict(), "dict(entity) must match to_dict()"
    return True


def test_procedural_unit_json_valid():
    """ProceduralUnit.__json__() returns valid JSON parseable back to a dict."""
    pu = ProceduralUnit(id="pu3", name="JSON skill", description="test")
    raw = pu.__json__()
    parsed = json.loads(raw)
    assert isinstance(parsed, dict), f"Expected dict from JSON, got {type(parsed)}"
    assert parsed == pu.to_dict(), "JSON round-trip must match to_dict()"
    return True


def test_procedural_unit_get_label():
    """ProceduralUnit.get_label() returns 'ProceduralUnit'."""
    pu = ProceduralUnit(id="l1", name="LabelTest")
    assert pu.get_label() == "ProceduralUnit", f"Expected 'ProceduralUnit', got '{pu.get_label()}'"
    return True


def test_procedural_unit_get_properties_excludes_none():
    """ProceduralUnit.get_properties() excludes None-valued keys."""
    pu = ProceduralUnit(id="p1", name="Props", description=None, proficiencyLevel=None)
    props = pu.get_properties()
    assert "description" not in props, "description=None should be excluded"
    assert "proficiencyLevel" not in props, "proficiencyLevel=None should be excluded"
    assert "id" in props
    assert "name" in props
    return True


def test_procedural_unit_get_properties_includes_falsy():
    """ProceduralUnit.get_properties() includes 0, empty string, empty list."""
    pu = ProceduralUnit(id="", name="", proficiencyLevel=0, concepts=[])
    props = pu.get_properties()
    assert "id" in props, "Empty string 'id' should be present"
    assert "name" in props, "Empty string 'name' should be present"
    assert "proficiencyLevel" in props, "0-valued 'proficiencyLevel' should be present"
    assert "concepts" in props, "Empty list 'concepts' should be present"
    assert props["id"] == ""
    assert props["proficiencyLevel"] == 0
    return True


def test_procedural_unit_list_to_dicts():
    """ProceduralUnit.list_to_dicts() converts a list of entity objects."""
    units = [
        ProceduralUnit(id="x1", name="First"),
        ProceduralUnit(id="x2", name="Second", steps="Do this"),
    ]
    result = ProceduralUnit.list_to_dicts(units)
    assert len(result) == 2
    assert all(isinstance(d, dict) for d in result)
    assert result[0]["id"] == "x1"
    assert result[1]["id"] == "x2"
    return True


def test_procedural_unit_list_to_dicts_mixed():
    """ProceduralUnit.list_to_dicts() handles mixed entities and plain dicts."""
    items = [
        ProceduralUnit(id="m1", name="Mixed1"),
        {"id": "plain", "name": "PlainDict"},
        ProceduralUnit(id="m2", name="Mixed2"),
    ]
    result = ProceduralUnit.list_to_dicts(items)
    assert len(result) == 3
    assert result[1] == {"id": "plain", "name": "PlainDict"}
    assert result[2]["id"] == "m2"
    return True


def test_procedural_unit_str_repr():
    """ProceduralUnit.__str__() and __repr__() return non-empty strings."""
    pu = ProceduralUnit(id="sr1", name="StringTest")
    s = str(pu)
    r = repr(pu)
    assert isinstance(s, str) and len(s) > 0, "__str__() must return non-empty string"
    assert isinstance(r, str) and len(r) > 0, "__repr__() must return non-empty string"
    return True


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))