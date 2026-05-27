"""
Tests for lib/services/ai_pipeline.py

Covers:
- resolve_thinking_mode() — all priority branches (pure function, no deps)
- AIPipeline._to_provider_messages() — image injection into last user message
- AIPipeline.run() — full lifecycle with mocked provider and services
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.services.ai_pipeline import resolve_thinking_mode, AIPipeline, AIPipelineRequest
from lib.ai_providers.base_provider import ChatResponse


# ---------------------------------------------------------------------------
# resolve_thinking_mode — pure function, no mocking needed
# ---------------------------------------------------------------------------

def test_thinking_override_true():
    """Explicit override=True always enables thinking regardless of config."""
    use, model = resolve_thinking_mode(
        content="simple question",
        default_model="llama3.2",
        thinking_cfg={"model": "llama3.2-think", "default_enabled": False, "trigger_words": []},
        override=True,
    )
    assert use, f"Expected use=True, got {use}"
    assert model == "llama3.2-think", f"Expected 'llama3.2-think', got '{model}'"


def test_thinking_override_false():
    """Explicit override=False disables thinking even when default_enabled is True."""
    use, model = resolve_thinking_mode(
        content="think about this",
        default_model="llama3.2",
        thinking_cfg={"model": "llama3.2-think", "default_enabled": True, "trigger_words": ["think"]},
        override=False,
    )
    assert not use, f"Expected use=False, got {use}"
    assert model == "llama3.2", f"Expected 'llama3.2', got '{model}'"


def test_thinking_default_enabled():
    """default_enabled: true activates thinking with no trigger word needed."""
    use, model = resolve_thinking_mode(
        content="how are you?",
        default_model="llama3.2",
        thinking_cfg={"model": "llama3.2-think", "default_enabled": True},
        override=None,
    )
    assert use, f"Expected use=True, got {use}"
    assert model == "llama3.2-think", f"Expected 'llama3.2-think', got '{model}'"


def test_thinking_trigger_word():
    """Trigger word in message activates thinking mode."""
    use, model = resolve_thinking_mode(
        content="Please THINK carefully about this problem.",
        default_model="llama3.2",
        thinking_cfg={"model": "llama3.2-think", "default_enabled": False, "trigger_words": ["think"]},
        override=None,
    )
    assert use, f"Expected use=True, got {use}"
    assert model == "llama3.2-think", f"Expected 'llama3.2-think', got '{model}'"


def test_thinking_no_trigger():
    """No trigger word, no override → standard model."""
    use, model = resolve_thinking_mode(
        content="Hello!",
        default_model="llama3.2",
        thinking_cfg={"model": "llama3.2-think", "default_enabled": False, "trigger_words": ["think", "analyze"]},
        override=None,
    )
    assert not use, f"Expected use=False, got {use}"
    assert model == "llama3.2", f"Expected 'llama3.2', got '{model}'"


def test_thinking_empty_config():
    """Empty thinking config should default to False and keep default model."""
    use, model = resolve_thinking_mode(
        content="think hard",
        default_model="llama3.2",
        thinking_cfg={},
        override=None,
    )
    assert not use, f"Expected use=False, got {use}"
    assert model == "llama3.2", f"Expected 'llama3.2', got '{model}'"


# ---------------------------------------------------------------------------
# AIPipeline._to_provider_messages — image injection
# ---------------------------------------------------------------------------

def test_to_provider_images_injected_into_last_user():
    """image_urls should be injected only into the LAST user message."""
    pipeline = AIPipeline()
    msgs = [
        {"role": "system", "content": "You are Nami."},
        {"role": "user", "content": "first message"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "second message with image"},
    ]
    result = pipeline._to_provider_messages(msgs, image_urls=["data:img1", "data:img2"])
    last = result[-1]
    assert last.images == ["data:img1", "data:img2"], f"last.images={last.images}"
    assert result[1].images is None, f"earlier user message got images: {result[1].images}"


def test_to_provider_no_images():
    """No image_urls → no images on any message."""
    pipeline = AIPipeline()
    msgs = [{"role": "user", "content": "hello"}]
    result = pipeline._to_provider_messages(msgs, image_urls=[])
    assert result[0].images is None, f"images={result[0].images}"


def test_to_provider_preserves_roles():
    """All roles and content are preserved after conversion."""
    pipeline = AIPipeline()
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user"},
        {"role": "assistant", "content": "asst"},
    ]
    result = pipeline._to_provider_messages(msgs, image_urls=[])
    result_tuples = [(m.role, m.content) for m in result]
    expected = [("system", "sys"), ("user", "user"), ("assistant", "asst")]
    assert result_tuples == expected, f"Expected {expected}, got {result_tuples}"


# ---------------------------------------------------------------------------
# AIPipeline.run() — full lifecycle with mocks
# ---------------------------------------------------------------------------

async def _run_pipeline_mock(think_override=None, tool_calls=None):
    """Helper: run pipeline with a mocked provider and g_data services."""
    mock_provider = MagicMock()
    mock_provider.supports_vision.return_value = False
    mock_response = ChatResponse(content="Hello there!", tool_calls=tool_calls, model="llama3.2")
    mock_provider.chat = AsyncMock(return_value=mock_response)

    mock_ctx_builder = MagicMock()
    mock_ctx_builder.build_context = AsyncMock(return_value=[{"role": "user", "content": "hi"}])

    mock_config = MagicMock()
    mock_config.data = {"thinking": {}, "bot": {}}

    mock_model_cache = MagicMock()

    g_data_patch = {
        "cfg": mock_config,
        "context_builder": mock_ctx_builder,
        "vision_service": None,
        "tools": [],
        "model_cache": mock_model_cache,
    }

    with patch("lib.services.ai_pipeline.g_data") as mock_g:
        mock_g.get = lambda key, default=None: g_data_patch.get(key, default)
        with patch("lib.services.ai_pipeline.process_memories"):
            pipeline = AIPipeline()
            result = await pipeline.run(
                AIPipelineRequest(
                    messages=[{"role": "user", "content": "hi"}],
                    user_id="test:user1",
                    enable_memory=False,
                    think_override=think_override,
                ),
                provider=mock_provider,
                model_name="llama3.2",
                full_model_ref="ollama/llama3.2",
                original_user_msg="hi",
            )
    return result


def test_pipeline_run_basic():
    """Pipeline returns the provider's content."""
    result = asyncio.run(_run_pipeline_mock())
    assert result.content == "Hello there!", f"content={result.content!r}"
    assert result.model_used == "llama3.2", f"model_used={result.model_used!r}"


def test_pipeline_run_thinking_override():
    """Pipeline respects think_override=True and switches to thinking model."""

    async def _run():
        mock_provider = MagicMock()
        mock_provider.supports_vision.return_value = False
        mock_response = ChatResponse(content="Deep answer.", model="llama3.2-think")
        mock_provider.chat = AsyncMock(return_value=mock_response)

        mock_ctx_builder = MagicMock()
        mock_ctx_builder.build_context = AsyncMock(return_value=[{"role": "user", "content": "solve this"}])

        mock_config = MagicMock()
        mock_config.data = {"thinking": {"model": "llama3.2-think", "default_enabled": False, "trigger_words": []}, "bot": {}}

        g_data_patch = {
            "cfg": mock_config,
            "context_builder": mock_ctx_builder,
            "vision_service": None,
            "tools": [],
            "model_cache": MagicMock(),
        }

        with patch("lib.services.ai_pipeline.g_data") as mock_g:
            mock_g.get = lambda key, default=None: g_data_patch.get(key, default)
            with patch("lib.services.ai_pipeline.process_memories"):
                pipeline = AIPipeline()
                return await pipeline.run(
                    AIPipelineRequest(
                        messages=[{"role": "user", "content": "solve this"}],
                        user_id="test:user1",
                        enable_memory=False,
                        think_override=True,
                    ),
                    provider=mock_provider,
                    model_name="llama3.2",
                    full_model_ref="ollama/llama3.2",
                    original_user_msg="solve this",
                )

    result = asyncio.run(_run())
    assert result.model_used == "llama3.2-think", (
        f"model_used={result.model_used!r} (expected 'llama3.2-think')"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
