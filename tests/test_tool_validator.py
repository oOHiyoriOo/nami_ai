"""
Tests for lib/tool_argument_validator.py

Covers:
- Valid arguments pass through unchanged
- Missing required parameters are rejected
- Unexpected parameters are rejected
- Type mismatches are rejected
- String length, numeric range, nested object, array validation
- Security limits: MAX_STRING_LENGTH (50000), MAX_ARRAY_LENGTH (1000), MAX_OBJECT_KEYS (100), MAX_NESTING_DEPTH (10)
- Boundary values accepted at exact limits
- Enum validation (valid/invalid values)
- Regex pattern validation
- Bool-to-integer/number coercion
"""

import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.tool_argument_validator import validate_tool_arguments, ToolArgumentValidationError


def test_valid_arguments():
    """Test with valid arguments"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["query"],
        },
    }
    arguments = {"query": "test search", "limit": 10}
    result = validate_tool_arguments(tool_schema, arguments)
    assert result is not None


def test_missing_required():
    """Test missing required parameter"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }
    with pytest.raises(ToolArgumentValidationError):
        validate_tool_arguments(tool_schema, {})


def test_unexpected_parameter():
    """Test unexpected parameter"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }
    with pytest.raises(ToolArgumentValidationError):
        validate_tool_arguments(tool_schema, {"query": "test", "malicious_param": "injection"})


def test_type_mismatch():
    """Test type mismatch"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
        },
    }
    with pytest.raises(ToolArgumentValidationError):
        validate_tool_arguments(tool_schema, {"count": "not a number"})


def test_string_length():
    """Test string length validation"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "maxLength": 10}},
            "required": ["text"],
        },
    }
    with pytest.raises(ToolArgumentValidationError):
        validate_tool_arguments(tool_schema, {"text": "This is definitely longer than 10 characters"})


def test_numeric_range():
    """Test numeric range validation"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {"count": {"type": "integer", "minimum": 1, "maximum": 10}},
            "required": ["count"],
        },
    }
    with pytest.raises(ToolArgumentValidationError):
        validate_tool_arguments(tool_schema, {"count": 100})


def test_nested_object():
    """Test nested object validation"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "value": {"type": "string"},
                    },
                    "required": ["enabled"],
                }
            },
            "required": ["config"],
        },
    }
    result = validate_tool_arguments(tool_schema, {"config": {"enabled": True, "value": "test"}})
    assert result is not None


def test_array_validation():
    """Test array validation"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {
                "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 5}
            },
            "required": ["tags"],
        },
    }
    result = validate_tool_arguments(tool_schema, {"tags": ["tag1", "tag2", "tag3"]})
    assert result is not None


def test_string_exceeds_security_limit():
    """Test string exceeding MAX_STRING_LENGTH (50000) security limit"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }
    with pytest.raises(ToolArgumentValidationError, match="50000"):
        validate_tool_arguments(tool_schema, {"text": "x" * 50001})


def test_array_exceeds_security_limit():
    """Test array exceeding MAX_ARRAY_LENGTH (1000) security limit"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {"type": "integer"}}},
            "required": ["items"],
        },
    }
    with pytest.raises(ToolArgumentValidationError, match="1000"):
        validate_tool_arguments(tool_schema, {"items": list(range(1001))})


def test_object_exceeds_key_limit():
    """Test object exceeding MAX_OBJECT_KEYS (100) security limit"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {"config": {"type": "object"}},
            "required": ["config"],
        },
    }
    with pytest.raises(ToolArgumentValidationError, match="100"):
        validate_tool_arguments(tool_schema, {"config": {f"key{i}": i for i in range(101)}})


def test_nesting_exceeds_depth_limit():
    """Test nesting exceeding MAX_NESTING_DEPTH (10) security limit"""

    def build_nested_schema(n):
        schema = {"type": "object", "properties": {}}
        current = schema["properties"]
        for _ in range(n):
            current["nested"] = {"type": "object", "properties": {}}
            current = current["nested"]["properties"]
        return schema

    def build_nested_data(n):
        if n == 0:
            return {}
        return {"nested": build_nested_data(n - 1)}

    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {"data": build_nested_schema(11)},
            "required": ["data"],
        },
    }
    with pytest.raises(ToolArgumentValidationError, match="(?i)nesting depth"):
        validate_tool_arguments(tool_schema, {"data": build_nested_data(11)})


def test_string_at_exact_limit():
    """Test string at exactly MAX_STRING_LENGTH (50000) accepted"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }
    result = validate_tool_arguments(tool_schema, {"text": "x" * 50000})
    assert result["text"] == "x" * 50000, "[FAIL] Validation modified the string"


def test_array_at_exact_limit():
    """Test array at exactly MAX_ARRAY_LENGTH (1000) accepted"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {"type": "integer"}}},
            "required": ["items"],
        },
    }
    result = validate_tool_arguments(tool_schema, {"items": list(range(1000))})
    assert len(result["items"]) == 1000, "[FAIL] Validation modified the array"


def test_enum_valid():
    """Test enum validation with a matching value"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {"color": {"type": "string", "enum": ["red", "green", "blue"]}},
            "required": ["color"],
        },
    }
    result = validate_tool_arguments(tool_schema, {"color": "green"})
    assert result["color"] == "green", f"[FAIL] Value was modified: {result}"


def test_enum_invalid():
    """Test enum validation with a non-matching value"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {"color": {"type": "string", "enum": ["red", "green", "blue"]}},
            "required": ["color"],
        },
    }
    with pytest.raises(ToolArgumentValidationError, match="must be one of"):
        validate_tool_arguments(tool_schema, {"color": "yellow"})


def test_pattern_valid():
    """Test pattern validation with a matching value"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "pattern": r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",
                }
            },
            "required": ["email"],
        },
    }
    result = validate_tool_arguments(tool_schema, {"email": "user@example.com"})
    assert result["email"] == "user@example.com", f"[FAIL] Value was modified: {result}"


def test_pattern_invalid():
    """Test pattern validation with a non-matching value"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "pattern": r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",
                }
            },
            "required": ["email"],
        },
    }
    with pytest.raises(ToolArgumentValidationError, match="(?i)pattern"):
        validate_tool_arguments(tool_schema, {"email": "not-an-email"})


def test_enum_numeric_coercion():
    """Test enum with mixed types - integer not matched against string enum values"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string", "enum": ["100", "200", "300"]}},
            "required": ["code"],
        },
    }
    with pytest.raises(ToolArgumentValidationError, match="(?i)must be string"):
        validate_tool_arguments(tool_schema, {"code": 200})


def test_bool_coerced_to_integer_true():
    """Test that bool True is auto-coerced to integer 1"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {"count": {"type": "integer", "minimum": 0, "maximum": 10}},
            "required": ["count"],
        },
    }
    result = validate_tool_arguments(tool_schema, {"count": True})
    assert result["count"] == 1 and isinstance(result["count"], int) and not isinstance(result["count"], bool), \
        f"[FAIL] Expected integer 1, got {result['count']} (type: {type(result['count']).__name__})"


def test_bool_coerced_to_integer_false():
    """Test that bool False is auto-coerced to integer 0"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {"count": {"type": "integer", "minimum": 0, "maximum": 10}},
            "required": ["count"],
        },
    }
    result = validate_tool_arguments(tool_schema, {"count": False})
    assert result["count"] == 0 and isinstance(result["count"], int) and not isinstance(result["count"], bool), \
        f"[FAIL] Expected integer 0, got {result['count']} (type: {type(result['count']).__name__})"


def test_bool_coerced_to_number():
    """Test that bool is auto-coerced to float for number type"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {"ratio": {"type": "number", "minimum": 0.0, "maximum": 1.0}},
            "required": ["ratio"],
        },
    }
    result = validate_tool_arguments(tool_schema, {"ratio": True})
    assert result["ratio"] == 1.0 and isinstance(result["ratio"], float), \
        f"[FAIL] Expected float 1.0, got {result['ratio']} (type: {type(result['ratio']).__name__})"


def test_bool_coerced_respects_range():
    """Test that coerced bool still respects range constraints (True=1, minimum=5 → fail)"""
    tool_schema = {
        "name": "test_tool",
        "parameters": {
            "type": "object",
            "properties": {"count": {"type": "integer", "minimum": 5}},
            "required": ["count"],
        },
    }
    with pytest.raises(ToolArgumentValidationError, match=">= 5"):
        validate_tool_arguments(tool_schema, {"count": True})
