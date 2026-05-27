"""
Test suite for parse_model_string() in lib/utils/model_string.py
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.utils.model_string import parse_model_string


# ---------------------------------------------------------------------------
# Valid input tests
# ---------------------------------------------------------------------------

def test_ollama_llama2():
    """parse_model_string("ollama/llama2") → ("ollama", "llama2")"""
    assert parse_model_string("ollama/llama2") == ("ollama", "llama2")


def test_copilot_gpt4_1():
    """parse_model_string("copilot/gpt-4.1") → ("copilot", "gpt-4.1")"""
    assert parse_model_string("copilot/gpt-4.1") == ("copilot", "gpt-4.1")


def test_openai_gpt4_turbo():
    """Model name contains hyphen."""
    assert parse_model_string("openai/gpt-4-turbo") == ("openai", "gpt-4-turbo")


def test_ollama_deepseek_r1_7b():
    """Model name contains colon."""
    assert parse_model_string("ollama/deepseek-r1:7b") == ("ollama", "deepseek-r1:7b")


def test_splits_on_first_slash_only():
    """parse_model_string("a/b/c") → ("a", "b/c") — splits on first / only."""
    assert parse_model_string("a/b/c") == ("a", "b/c")


# ---------------------------------------------------------------------------
# Invalid input tests
# ---------------------------------------------------------------------------

def test_no_slash_raises_value_error():
    """parse_model_string("invalid") raises ValueError with descriptive message."""
    with pytest.raises(ValueError, match=r"Invalid model format: 'invalid'"):
        parse_model_string("invalid")


def test_empty_string_raises_value_error():
    """parse_model_string("") raises ValueError."""
    with pytest.raises(ValueError, match=r"Invalid model format: ''"):
        parse_model_string("")
