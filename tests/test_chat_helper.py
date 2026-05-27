"""
Tests for lib/chat_helper.py

Covers:
- format_user_message: normal inputs produce correctly formatted string
- format_user_message: empty name handled gracefully
- format_user_message: empty content handled gracefully
- format_user_message: special characters preserved literally
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.chat_helper import format_user_message


def test_normal_inputs():
    """Normal inputs → correctly formatted message"""
    result = format_user_message("Alice", "2026-01-01 12:00:00", "Hello")
    expected = "Alice [2026-01-01 12:00:00] : Hello"
    assert result == expected, f"Expected {expected!r}, got {result!r}"


def test_empty_name():
    """Empty name → space before bracket, content preserved"""
    result = format_user_message("", "2026-01-01 12:00:00", "Hello")
    expected = " [2026-01-01 12:00:00] : Hello"
    assert result == expected, f"Expected {expected!r}, got {result!r}"


def test_empty_content():
    """Empty content → name and timestamp still formatted"""
    result = format_user_message("Alice", "2026-01-01 12:00:00", "")
    expected = "Alice [2026-01-01 12:00:00] : "
    assert result == expected, f"Expected {expected!r}, got {result!r}"


def test_special_characters():
    """Special characters in name/content preserved literally"""
    name = 'User[1]: "Bot"'
    content = "<tag> [nested: brackets] : text"
    result = format_user_message(name, "2026-01-01 12:00:00", content)
    expected = 'User[1]: "Bot" [2026-01-01 12:00:00] : <tag> [nested: brackets] : text'
    assert result == expected, f"Expected {expected!r}, got {result!r}"


def test_empty_timestamp():
    """Edge case: empty timestamp still formats"""
    result = format_user_message("Alice", "", "Hello")
    expected = "Alice [] : Hello"
    assert result == expected, f"Expected {expected!r}, got {result!r}"


def test_all_empty():
    """Edge case: all empty inputs"""
    result = format_user_message("", "", "")
    expected = " [] : "
    assert result == expected, f"Expected {expected!r}, got {result!r}"


def test_unicode_characters():
    """Unicode characters in name and content preserved"""
    name = "アリス"
    content = "こんにちは 🌍"
    result = format_user_message(name, "2026-01-01 12:00:00", content)
    expected = "アリス [2026-01-01 12:00:00] : こんにちは 🌍"
    assert result == expected, f"Expected {expected!r}, got {result!r}"


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
