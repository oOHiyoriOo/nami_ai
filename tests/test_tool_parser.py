"""
Tests for lib/utils/tool_parser.py — extract_tool_from_xml()

Covers:
- Valid <tool_call> with name + args → structured response
- Response without tool_call tags → original response returned unchanged
- Invalid JSON inside <tool_call> → ValueError with "invalid JSON"
- Missing 'name' field → ValueError
- Missing both 'args' and 'arguments' → ValueError
- Tool call is not a JSON object (string or list) → ValueError
- args is not a dict → ValueError
- Empty message.content → original response unchanged
- Response with 'arguments' key (alternative to 'args') → correctly used
"""

import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.utils.tool_parser import extract_tool_from_xml


def test_valid_xml_tool_call():
    """Valid <tool_call> with name, args, and arguments fields → returns structured response."""
    response = {
        "model": "test_model",
        "created_at": "2024-01-01T00:00:00Z",
        "message": {
            "role": "assistant",
            "content": '<tool_call>{"name": "my_tool", "args": {"key": "value"}, "arguments": {"other": "extra"}}</tool_call>',
        },
    }
    result = extract_tool_from_xml(response)
    assert result["model"] == "test_model"
    assert result["created_at"] == "2024-01-01T00:00:00Z"
    assert result["message"]["role"] == "assistant"
    assert result["message"]["content"] == ""
    tool_calls = result["message"]["tool_calls"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["function"]["name"] == "my_tool"
    assert tool_calls[0]["function"]["arguments"] == {"key": "value"}


def test_no_tool_call_tags():
    """Response without any <tool_call> tags → returns original response unchanged."""
    response = {
        "model": "test_model",
        "created_at": "2024-01-01T00:00:00Z",
        "message": {"role": "assistant", "content": "Hello, how can I help you today?"},
    }
    result = extract_tool_from_xml(response)
    assert result is response


def test_invalid_json_in_tool_call():
    """Invalid JSON inside <tool_call> → raises ValueError with 'invalid JSON' in message."""
    response = {
        "model": "test_model",
        "created_at": "2024-01-01T00:00:00Z",
        "message": {"role": "assistant", "content": "<tool_call>{invalid json!!!}</tool_call>"},
    }
    with pytest.raises(ValueError, match="invalid JSON"):
        extract_tool_from_xml(response)


def test_missing_name_field():
    """Missing 'name' field → raises ValueError."""
    response = {
        "model": "test_model",
        "created_at": "2024-01-01T00:00:00Z",
        "message": {
            "role": "assistant",
            "content": '<tool_call>{"args": {"key": "value"}}</tool_call>',
        },
    }
    with pytest.raises(ValueError, match="(?i)missing 'name'"):
        extract_tool_from_xml(response)


def test_missing_args_and_arguments():
    """Missing both 'args' and 'arguments' → raises ValueError."""
    response = {
        "model": "test_model",
        "created_at": "2024-01-01T00:00:00Z",
        "message": {
            "role": "assistant",
            "content": '<tool_call>{"name": "my_tool"}</tool_call>',
        },
    }
    with pytest.raises(ValueError, match="missing 'args' or 'arguments'"):
        extract_tool_from_xml(response)


def test_tool_call_not_json_object_string():
    """Tool call that is a JSON string (not an object) → raises ValueError."""
    response = {
        "model": "test_model",
        "created_at": "2024-01-01T00:00:00Z",
        "message": {"role": "assistant", "content": '<tool_call>"just a string"</tool_call>'},
    }
    with pytest.raises(ValueError, match="must be a JSON object"):
        extract_tool_from_xml(response)


def test_tool_call_not_json_object_list():
    """Tool call that is a JSON list (not an object) → raises ValueError."""
    response = {
        "model": "test_model",
        "created_at": "2024-01-01T00:00:00Z",
        "message": {"role": "assistant", "content": '<tool_call>["item1", "item2"]</tool_call>'},
    }
    with pytest.raises(ValueError, match="must be a JSON object"):
        extract_tool_from_xml(response)


def test_args_not_a_dict():
    """Tool arguments that are not a dict → raises ValueError."""
    response = {
        "model": "test_model",
        "created_at": "2024-01-01T00:00:00Z",
        "message": {
            "role": "assistant",
            "content": '<tool_call>{"name": "my_tool", "args": "not a dict"}</tool_call>',
        },
    }
    with pytest.raises(ValueError, match="must be a JSON object"):
        extract_tool_from_xml(response)


def test_empty_message_content():
    """Response with empty message.content → returns original response unchanged."""
    response = {
        "model": "test_model",
        "created_at": "2024-01-01T00:00:00Z",
        "message": {"role": "assistant", "content": ""},
    }
    result = extract_tool_from_xml(response)
    assert result is response


def test_arguments_key_alternative():
    """Response with 'arguments' key (alternative to 'args') → correctly uses arguments."""
    response = {
        "model": "test_model",
        "created_at": "2024-01-01T00:00:00Z",
        "message": {
            "role": "assistant",
            "content": '<tool_call>{"name": "my_tool", "arguments": {"key": "from_arguments"}}</tool_call>',
        },
    }
    result = extract_tool_from_xml(response)
    tool_calls = result["message"]["tool_calls"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["function"]["name"] == "my_tool"
    assert tool_calls[0]["function"]["arguments"] == {"key": "from_arguments"}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
