"""
Test script for Location class (neo4j_lib) — to_cypher() method
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.neo4j_lib.location import Location


def test_to_cypher_default_alias():
    """to_cypher() produces valid Cypher syntax with default alias 'loc'."""
    loc = Location(id="123", name="Home")
    result = loc.to_cypher()
    expected_start = "(loc:Location {"
    expected_end = "})"
    assert result.startswith(expected_start), f"Expected start '{expected_start}', got '{result}'"
    assert result.endswith(expected_end), f"Expected end '{expected_end}', got '{result}'"
    assert 'id: "123"' in result
    assert 'name: "Home"' in result
    return True


def test_to_cypher_custom_alias():
    """to_cypher(alias="x") uses custom alias."""
    loc = Location(id="abc", name="Office")
    result = loc.to_cypher(alias="x")
    assert result.startswith("(x:Location {"), f"Expected '(x:Location {{', got '{result}'"
    assert 'id: "abc"' in result
    assert 'name: "Office"' in result
    return True


def test_to_cypher_skips_none_properties():
    """to_cypher() skips None properties (description, planeOfExistence)."""
    loc = Location(id="1", name="Test", description=None, planeOfExistence=None)
    result = loc.to_cypher()
    assert "description" not in result, f"description should be absent: {result}"
    assert "planeOfExistence" not in result, f"planeOfExistence should be absent: {result}"
    assert 'id: "1"' in result
    assert 'name: "Test"' in result
    return True


def test_to_cypher_includes_plane_of_existence():
    """to_cypher() includes planeOfExistence when set."""
    loc = Location(id="xyz", name="PlaneWalker", planeOfExistence="Astral")
    result = loc.to_cypher()
    assert 'planeOfExistence: "Astral"' in result, f"Expected planeOfExistence in: {result}"
    return True


def test_to_cypher_id_and_name_only():
    """to_cypher() with only id and name set → (loc:Location {id: "...", name: "..."})."""
    loc = Location(id="42", name="Base")
    # description and planeOfExistence default to None
    result = loc.to_cypher()
    assert result == '(loc:Location {id: "42", name: "Base"})', f"Unexpected result: {result}"
    return True


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))