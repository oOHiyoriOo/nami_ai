"""Tests for the MemoryGrooming heartbeat module."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from lib.services.heartbeat_modules.memory_grooming import MemoryGrooming


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(overrides: dict | None = None) -> MagicMock:
    """Create a mock ConfigurationFile with heartbeat.memory_grooming config."""
    cfg = MagicMock()
    cfg.data = {
        "heartbeat": {
            "modules": {
                "memory_grooming": overrides or {},
            }
        }
    }
    return cfg


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

class TestInit:
    def test_defaults_when_no_config(self):
        """No config passed — all defaults should apply."""
        mod = MemoryGrooming()
        assert mod.enabled is True
        assert mod.cooldown_seconds == 7200
        assert mod._min_new_memories == 5
        assert mod._provider_name == "ollama"
        assert mod._model_name == "llama3.2"
        assert mod._max_tool_calls == 8
        assert mod._auto_merge is False

    def test_config_overrides(self):
        """Config should override all defaults."""
        cfg = _make_config({
            "enabled": False,
            "cooldown": 3600,
            "min_new_memories": 10,
            "provider": "openai",
            "model": "gpt-4",
            "max_tool_calls": 16,
            "auto_merge": True,
        })
        mod = MemoryGrooming(config=cfg)
        assert mod.enabled is False
        assert mod.cooldown_seconds == 3600
        assert mod._min_new_memories == 10
        assert mod._provider_name == "openai"
        assert mod._model_name == "gpt-4"
        assert mod._max_tool_calls == 16
        assert mod._auto_merge is True

    def test_partial_config(self):
        """Partial config should override only given values."""
        cfg = _make_config({"min_new_memories": 3, "auto_merge": True})
        mod = MemoryGrooming(config=cfg)
        assert mod._min_new_memories == 3
        assert mod._auto_merge is True
        assert mod._provider_name == "ollama"  # default preserved
        assert mod._model_name == "llama3.2"  # default preserved


# ---------------------------------------------------------------------------
# Report parsing
# ---------------------------------------------------------------------------

class TestParseReport:
    def test_parse_full_report(self):
        """Parse a complete AI grooming report with all sections."""
        text = """Here's my analysis:

## CONTRADICTIONS
- Memory abc-123 says user uses Python, but memory def-456 says user hates Python
- Memory ghi-789 says project is active, memory jkl-012 says project was abandoned

## NEAR_DUPLICATES
- Memory mno-345 and pqr-678 both describe the same login bug (95% overlap)
- Memory stu-901 and vwx-234 both mention the project deployment date

## STALE_FLAGGED
- Memory yza-567 (EpisodicMemory): old greeting from 3 months ago, no connections
- Memory bcd-890 (KnowledgeUnit): outdated API version reference

## KNOWLEDGE_GAPS
- User mentioned "Project Phoenix" in 3 memories but no memory defines what it is
- Config references "retry_strategy" value but no explanation of why it was chosen

## SUMMARY
Found: 2 contradictions, 2 near-duplicates, 2 stale, 2 knowledge gaps. All are suggestions only.
"""
        mod = MemoryGrooming()
        report = mod._parse_report(text)

        assert len(report["contradictions"]) == 2
        assert "abc-123" in report["contradictions"][0]
        assert len(report["near_duplicates"]) == 2
        assert "mno-345" in report["near_duplicates"][0]
        assert len(report["stale_flagged"]) == 2
        assert "yza-567" in report["stale_flagged"][0]
        assert len(report["knowledge_gaps"]) == 2
        assert "Project Phoenix" in report["knowledge_gaps"][0]
        assert "2 contradictions" in report["summary"]

    def test_parse_empty_report(self):
        """Parse a report with 'None found' in all sections."""
        text = """Memory grooming complete.

## CONTRADICTIONS
None found

## NEAR_DUPLICATES
None found

## STALE_FLAGGED
None found

## KNOWLEDGE_GAPS
None found

## SUMMARY
All clear - no issues found.
"""
        mod = MemoryGrooming()
        report = mod._parse_report(text)

        assert report["contradictions"] == []
        assert report["near_duplicates"] == []
        assert report["stale_flagged"] == []
        assert report["knowledge_gaps"] == []
        assert "All clear" in report["summary"]

    def test_parse_mixed_case_headers(self):
        """Headers should match case-insensitively."""
        text = """## contradictions
- Something wrong

## near_duplicates
- Two similar things

## stale_flagged
- Old thing

## knowledge_gaps
- Missing info

## summary
All good.
"""
        mod = MemoryGrooming()
        report = mod._parse_report(text)
        assert len(report["contradictions"]) == 1
        assert len(report["near_duplicates"]) == 1
        assert len(report["stale_flagged"]) == 1
        assert len(report["knowledge_gaps"]) == 1

    def test_parse_numbered_items(self):
        """Numbered list items should be parsed correctly."""
        text = """## CONTRADICTIONS
1. First contradiction about memory abc
2. Second contradiction about memory def

## SUMMARY
Two contradictions found.
"""
        mod = MemoryGrooming()
        report = mod._parse_report(text)
        assert len(report["contradictions"]) == 2
        assert "First contradiction" in report["contradictions"][0]
        assert "Second contradiction" in report["contradictions"][1]

    def test_parse_partial_sections(self):
        """Only some sections present should still work."""
        text = """## CONTRADICTIONS
- Memory a and b conflict about user preference

## SUMMARY
1 contradiction found.
"""
        mod = MemoryGrooming()
        report = mod._parse_report(text)
        assert len(report["contradictions"]) == 1
        assert report["near_duplicates"] == []
        assert report["stale_flagged"] == []
        assert report["knowledge_gaps"] == []

    def test_parse_no_sections(self):
        """No recognizable sections should return empty dict."""
        text = "Just some random text with no structured headers."
        mod = MemoryGrooming()
        report = mod._parse_report(text)
        assert report["contradictions"] == []
        assert report["near_duplicates"] == []
        assert report["stale_flagged"] == []
        assert report["knowledge_gaps"] == []
        assert report["summary"] == ""


# ---------------------------------------------------------------------------
# Condition
# ---------------------------------------------------------------------------

class TestCondition:
    @pytest.mark.asyncio
    async def test_skips_when_no_memory_db(self):
        """condition() should return False when memory_db not in g_data."""
        with patch("lib.services.heartbeat_modules.memory_grooming.g_data") as mock_gd:
            mock_gd.get.return_value = None
            mod = MemoryGrooming()
            # Skip _init_db by setting _db_initialised
            mod._db_initialised = True
            result = await mod.condition()
            assert result is False

    @pytest.mark.asyncio
    async def test_skips_when_not_enough_new_memories(self):
        """condition() should return False when new memories < threshold."""
        with patch("lib.services.heartbeat_modules.memory_grooming.g_data") as mock_gd:
            mock_memory_db = MagicMock()
            mock_gd.get.return_value = mock_memory_db

            mod = MemoryGrooming()
            mod._db_initialised = True
            mod._min_new_memories = 5

            # Mock SQLite state to return old timestamp (so all memories are "new")
            mod._get_state = AsyncMock(return_value=0.0)
            # Mock _count_new_memories to return too few
            mod._count_new_memories = AsyncMock(return_value=2)

            result = await mod.condition()
            assert result is False

    @pytest.mark.asyncio
    async def test_passes_when_enough_new_memories(self):
        """condition() should return True when enough new memories exist."""
        with patch("lib.services.heartbeat_modules.memory_grooming.g_data") as mock_gd:
            mock_memory_db = MagicMock()
            mock_gd.get.return_value = mock_memory_db

            mod = MemoryGrooming()
            mod._db_initialised = True
            mod._min_new_memories = 5
            mod._get_state = AsyncMock(return_value=0.0)
            mod._count_new_memories = AsyncMock(return_value=10)
            mod._set_state = AsyncMock()

            result = await mod.condition()
            assert result is True


# ---------------------------------------------------------------------------
# Action
# ---------------------------------------------------------------------------

class TestAction:
    @pytest.mark.asyncio
    async def test_action_handles_no_memory_db(self):
        """action() should silently return if no memory_db."""
        mod = MemoryGrooming()
        with patch("lib.services.heartbeat_modules.memory_grooming.g_data") as mock_gd:
            mock_gd.get.return_value = None
            # Should not raise
            await mod.action()

    @pytest.mark.asyncio
    async def test_action_runs_grooming_agent(self):
        """action() schedules _run_locked_grooming behind the AI lock (fire-and-forget)."""
        mod = MemoryGrooming()

        with patch("lib.services.heartbeat_modules.memory_grooming.g_data") as mock_gd:
            mock_memory_db = MagicMock()
            # Return None for ai_lock (fallback path), mock_db for everything else
            mock_gd.get.side_effect = lambda key: None if key == "ai_lock" else mock_memory_db
            mod._run_locked_grooming = AsyncMock()
            await mod.action()
            await asyncio.sleep(0)  # flush event loop so the created task runs

        assert mod._run_locked_grooming.called

    @pytest.mark.asyncio
    async def test_action_handles_agent_error_gracefully(self):
        """action() does not propagate exceptions from the grooming task."""
        mod = MemoryGrooming()

        with patch("lib.services.heartbeat_modules.memory_grooming.g_data") as mock_gd:
            mock_memory_db = MagicMock()
            mock_gd.get.side_effect = lambda key: None if key == "ai_lock" else mock_memory_db
            mod._run_locked_grooming = AsyncMock(side_effect=RuntimeError("AI failed"))
            await mod.action()
            await asyncio.sleep(0)  # flush; exception is swallowed by the task

        # last_report unchanged — the error was contained in the fire-and-forget task
        assert mod.last_report == {}


# ---------------------------------------------------------------------------
# Integration: _run_grooming_agent
# ---------------------------------------------------------------------------

class TestRunGroomingAgent:
    @pytest.mark.asyncio
    async def test_no_config_raises(self):
        """_run_grooming_agent should raise RuntimeError when no cfg in g_data."""
        mod = MemoryGrooming()
        with patch("lib.services.heartbeat_modules.memory_grooming.g_data") as mock_gd:
            mock_gd.get.return_value = None
            with pytest.raises(RuntimeError, match="No config"):
                await mod._run_grooming_agent()

    @pytest.mark.asyncio
    async def test_ai_call_with_tools(self):
        """Full AI call flow: provider responds, tool loop executes, report parsed."""
        mod = MemoryGrooming()
        mod._provider_name = "ollama"
        mod._model_name = "test-model"
        mod._max_tool_calls = 4

        with (
            patch("lib.services.heartbeat_modules.memory_grooming.g_data") as mock_gd,
            patch("lib.ai_providers.ProviderRegistry") as mock_registry,
            patch("lib.services.tool_executor.execute_tool_loop") as mock_loop,
            patch("OllamaTools.dream_tools.get_tool") as mock_tools,
        ):
            # Mock config
            mock_cfg = MagicMock()
            mock_cfg.data = {"providers": {"ollama": {"base_url": "http://localhost:11434"}}}
            mock_gd.get.return_value = mock_cfg

            # Mock provider
            mock_provider = AsyncMock()
            mock_registry.get_provider.return_value = mock_provider

            # Mock tools
            mock_tools.return_value = []

            # First response has tool_calls → loop runs
            first_resp = MagicMock()
            first_resp.tool_calls = [{"function": {"name": "dream_get_stats"}}]
            mock_provider.chat.return_value = first_resp

            # Final response after tool loop
            final_resp = MagicMock()
            final_resp.content = """## CONTRADICTIONS
- Memory a and b conflict

## SUMMARY
1 contradiction."""
            mock_loop.return_value = (final_resp, [])

            report = await mod._run_grooming_agent()

            assert len(report["contradictions"]) == 1
            assert "1 contradiction" in report["summary"]
            assert mock_provider.chat.called
            assert mock_loop.called

    @pytest.mark.asyncio
    async def test_ai_no_tool_calls_proceeds_directly(self):
        """If AI returns text with no tool_calls, parse it directly."""
        mod = MemoryGrooming()
        mod._provider_name = "ollama"
        mod._model_name = "test-model"

        with (
            patch("lib.services.heartbeat_modules.memory_grooming.g_data") as mock_gd,
            patch("lib.ai_providers.ProviderRegistry") as mock_registry,
            patch("OllamaTools.dream_tools.get_tool") as mock_tools,
        ):
            mock_cfg = MagicMock()
            mock_cfg.data = {"providers": {"ollama": {}}}
            mock_gd.get.return_value = mock_cfg

            mock_provider = AsyncMock()
            mock_registry.get_provider.return_value = mock_provider
            mock_tools.return_value = []

            resp = MagicMock()
            resp.tool_calls = None
            resp.content = "## SUMMARY\nAll clear."
            mock_provider.chat.return_value = resp

            report = await mod._run_grooming_agent()
            assert report["summary"] == "All clear."


# ---------------------------------------------------------------------------
# last_report property
# ---------------------------------------------------------------------------

class TestLastReport:
    def test_initial_last_report_is_empty(self):
        mod = MemoryGrooming()
        assert mod.last_report == {}

    def test_last_report_persists_after_action(self):
        mod = MemoryGrooming()
        mod._last_report = {"contradictions": ["test"]}
        assert mod.last_report == {"contradictions": ["test"]}
