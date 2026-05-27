"""
Test script for system prompt parser
"""

import sys
from pathlib import Path
import tempfile
import asyncio
import re

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from lib.system_prompt_parser import NamiSystemPrompt
    DEPENDENCIES_AVAILABLE = True
except ImportError as e:
    DEPENDENCIES_AVAILABLE = False
    MISSING_DEPENDENCY = str(e)


def test_prompt_load():
    """Test loading a system prompt file"""
    prompt_content = "Hello, I am an AI assistant."
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, encoding='utf-8') as f:
        f.write(prompt_content)
        temp_path = f.name
    try:
        prompt = NamiSystemPrompt(temp_path)
        assert prompt._raw_prompt == prompt_content, f"Raw prompt mismatch: {prompt._raw_prompt!r}"
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_prompt_time_placeholder():
    """Test time placeholder replacement"""
    prompt_content = "Current time is {{TIME}}"
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, encoding='utf-8') as f:
        f.write(prompt_content)
        temp_path = f.name
    try:
        prompt = NamiSystemPrompt(temp_path)
        parsed = asyncio.run(prompt.get_prompt())
        assert "{{TIME}}" not in parsed, f"{{TIME}} not replaced in: {parsed!r}"
        assert re.search(r'\d{2}:\d{2}:\d{2}', parsed), f"No time pattern found in: {parsed!r}"
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_prompt_date_placeholder():
    """Test date placeholder replacement"""
    prompt_content = "Today's date is {{DATE}}"
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, encoding='utf-8') as f:
        f.write(prompt_content)
        temp_path = f.name
    try:
        prompt = NamiSystemPrompt(temp_path)
        parsed = asyncio.run(prompt.get_prompt())
        assert "{{DATE}}" not in parsed, f"{{DATE}} not replaced in: {parsed!r}"
        assert re.search(r'\d{2}-\d{2}-\d{4}', parsed), f"No date pattern found in: {parsed!r}"
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_prompt_multiple_placeholders():
    """Test multiple placeholders in one prompt"""
    prompt_content = "The date is {{DATE}} and the time is {{TIME}}."
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, encoding='utf-8') as f:
        f.write(prompt_content)
        temp_path = f.name
    try:
        prompt = NamiSystemPrompt(temp_path)
        parsed = asyncio.run(prompt.get_prompt())
        has_date = re.search(r'\d{2}-\d{2}-\d{4}', parsed)
        has_time = re.search(r'\d{2}:\d{2}:\d{2}', parsed)
        no_placeholders = "{{DATE}}" not in parsed and "{{TIME}}" not in parsed
        assert has_date, f"No date pattern in: {parsed!r}"
        assert has_time, f"No time pattern in: {parsed!r}"
        assert no_placeholders, f"Placeholders remain in: {parsed!r}"
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_prompt_no_placeholders():
    """Test prompt without any placeholders"""
    prompt_content = "Hello, world! This has no placeholders."
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, encoding='utf-8') as f:
        f.write(prompt_content)
        temp_path = f.name
    try:
        prompt = NamiSystemPrompt(temp_path)
        parsed = asyncio.run(prompt.get_prompt())
        assert parsed == prompt_content, f"Content changed without placeholders: {parsed!r}"
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_template_not_mutated():
    """Test parsing twice gives same result (no mutation of template)"""
    prompt_content = "{{TIME}} and {{DATE}}"
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, encoding='utf-8') as f:
        f.write(prompt_content)
        temp_path = f.name
    try:
        prompt = NamiSystemPrompt(temp_path)
        parsed1 = asyncio.run(prompt.get_prompt())
        parsed2 = asyncio.run(prompt.get_prompt())
        t1_match = re.search(r'\d{2}:\d{2}:\d{2}', parsed1)
        t2_match = re.search(r'\d{2}:\d{2}:\d{2}', parsed2)
        raw_intact = "{{TIME}}" in prompt._raw_prompt
        assert t1_match, f"No time in first parse: {parsed1!r}"
        assert t2_match, f"No time in second parse: {parsed2!r}"
        assert raw_intact, "_raw_prompt should still contain {{TIME}}"
        assert "{{TIME}}" not in parsed1, f"{{TIME}} not replaced in parsed1"
        assert "{{TIME}}" not in parsed2, f"{{TIME}} not replaced in parsed2"
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_unknown_template_preserved():
    """Test that unknown {{templates}} are silently preserved in output"""
    prompt_content = "Feature flag {{nonexistent_method}} is not implemented."
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, encoding='utf-8') as f:
        f.write(prompt_content)
        temp_path = f.name
    try:
        prompt = NamiSystemPrompt(temp_path)
        parsed = asyncio.run(prompt.get_prompt())
        assert "{{nonexistent_method}}" in parsed, f"Unknown template was stripped: {parsed!r}"
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_unknown_and_known_templates_mixed():
    """Test that unknown templates are preserved while known ones are replaced"""
    prompt_content = "{{TIME}} and {{nonexistent_method}}"
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, encoding='utf-8') as f:
        f.write(prompt_content)
        temp_path = f.name
    try:
        prompt = NamiSystemPrompt(temp_path)
        parsed = asyncio.run(prompt.get_prompt())
        time_replaced = re.search(r'\d{2}:\d{2}:\d{2}', parsed) is not None
        unknown_preserved = "{{nonexistent_method}}" in parsed
        time_template_gone = "{{TIME}}" not in parsed
        assert time_replaced, f"{{TIME}} not replaced in: {parsed!r}"
        assert unknown_preserved, f"Unknown template stripped: {parsed!r}"
        assert time_template_gone, f"{{TIME}} still present in: {parsed!r}"
    finally:
        Path(temp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    import pytest
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
