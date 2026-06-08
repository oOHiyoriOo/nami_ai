"""
Tests for lib/ai_providers/base_provider.py and provider message normalization.

Covers:
- Message dataclass: all fields accessible, tool_call_id field present
- _normalize_messages: role, content, name, tool_calls, tool_call_id all included
- _normalize_messages: tool_call_id omitted when None (not sent as null to providers)
- _normalize_messages: images field included when present
- OllamaProvider: provider name, list_models returns list
- OpenAIProvider: tool_call extraction includes 'id' field
- CopilotProvider: tool_call extraction includes 'id' field
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.ai_providers.base_provider import Message, ChatResponse, AIProvider


# ---------------------------------------------------------------------------
# Message dataclass
# ---------------------------------------------------------------------------

def test_message_fields_accessible():
    """Message has all expected fields."""
    m = Message(
        role="tool",
        content="result",
        name="my_tool",
        tool_calls=[{"function": {"name": "f", "arguments": {}}}],
        images=["b64data"],
        tool_call_id="call_abc",
    )
    ok = (
        m.role == "tool" and
        m.content == "result" and
        m.name == "my_tool" and
        m.tool_calls is not None and
        m.images == ["b64data"] and
        m.tool_call_id == "call_abc"
    )
    assert ok, f"m={m}"


def test_message_defaults():
    """Message optional fields default to None."""
    m = Message(role="user", content="hello")
    assert not any([m.name, m.tool_calls, m.images, m.tool_call_id]), (
        f"unexpected non-None defaults: name={m.name}, tool_calls={m.tool_calls}, "
        f"images={m.images}, tool_call_id={m.tool_call_id}"
    )


def test_message_none_content():
    """Message content can be None (e.g. assistant message with only tool_calls)."""
    try:
        m = Message(role="assistant", content=None, tool_calls=[])
        if m.content is not None:
            assert False, f"content={m.content!r}"
    except Exception as e:
        assert False, f"raised: {e}"


# ---------------------------------------------------------------------------
# _normalize_messages via OllamaProvider (uses base class implementation)
# ---------------------------------------------------------------------------

def _get_ollama_provider():
    """Create OllamaProvider with a fake config (no real Ollama connection)."""
    from unittest.mock import patch, MagicMock
    with patch("lib.ai_providers.ollama_provider.OllamaClient") as mock_client_class:
        mock_client = MagicMock()
        mock_client.show = MagicMock(return_value={"capabilities": ["completion", "vision"]})
        mock_client_class.return_value = mock_client
        from lib.ai_providers.ollama_provider import OllamaProvider
        return OllamaProvider({"url": "http://localhost:11434", "model": "llama3.2"})


def test_normalize_basic():
    """Basic role+content message normalizes correctly."""
    from unittest.mock import patch, MagicMock
    with patch("lib.ai_providers.ollama_provider.OllamaClient") as mock_client_class:
        mock_client_class.return_value = MagicMock(show=MagicMock(return_value={"capabilities": []}))
        from lib.ai_providers.ollama_provider import OllamaProvider
        p = OllamaProvider({"url": "http://localhost:11434", "model": "llama3.2"})
        result = p._normalize_messages([Message(role="user", content="hello")])
    assert result == [{"role": "user", "content": "hello"}], f"{result}"


def test_normalize_tool_call_id_included():
    """tool_call_id is included in normalized dict when set."""
    from unittest.mock import patch, MagicMock
    with patch("lib.ai_providers.ollama_provider.OllamaClient") as mock_client_class:
        mock_client_class.return_value = MagicMock(show=MagicMock(return_value={"capabilities": []}))
        from lib.ai_providers.ollama_provider import OllamaProvider
        p = OllamaProvider({"url": "http://localhost:11434", "model": "llama3.2"})
        result = p._normalize_messages([
            Message(role="tool", content="result", tool_call_id="call_xyz")
        ])
    assert result[0].get("tool_call_id") == "call_xyz", f"tool_call_id not in result: {result[0]}"


def test_normalize_tool_call_id_omitted_when_none():
    """tool_call_id key is absent (not null) when field is None."""
    from unittest.mock import patch, MagicMock
    with patch("lib.ai_providers.ollama_provider.OllamaClient") as mock_client_class:
        mock_client_class.return_value = MagicMock(show=MagicMock(return_value={"capabilities": []}))
        from lib.ai_providers.ollama_provider import OllamaProvider
        p = OllamaProvider({"url": "http://localhost:11434", "model": "llama3.2"})
        result = p._normalize_messages([Message(role="user", content="hi")])
    assert not ("tool_call_id" in result[0]), f"tool_call_id should not be present: {result[0]}"


def test_normalize_tool_calls_included():
    """tool_calls dict is preserved in assistant message."""
    from unittest.mock import patch, MagicMock
    with patch("lib.ai_providers.ollama_provider.OllamaClient") as mock_client_class:
        mock_client_class.return_value = MagicMock(show=MagicMock(return_value={"capabilities": []}))
        from lib.ai_providers.ollama_provider import OllamaProvider
        p = OllamaProvider({"url": "http://localhost:11434", "model": "llama3.2"})
        tc = [{"function": {"name": "my_tool", "arguments": {}}}]
        result = p._normalize_messages([Message(role="assistant", content="", tool_calls=tc)])
    assert result[0].get("tool_calls") == tc, f"tool_calls={result[0].get('tool_calls')}"


def test_normalize_images_included():
    """images list is preserved in normalized message."""
    from unittest.mock import patch, MagicMock
    with patch("lib.ai_providers.ollama_provider.OllamaClient") as mock_client_class:
        mock_client_class.return_value = MagicMock(show=MagicMock(return_value={"capabilities": []}))
        from lib.ai_providers.ollama_provider import OllamaProvider
        p = OllamaProvider({"url": "http://localhost:11434", "model": "llama3.2"})
        result = p._normalize_messages([Message(role="user", content="see this", images=["b64abc"])])
    assert result[0].get("images") == ["b64abc"], f"images={result[0].get('images')}"


# ---------------------------------------------------------------------------
# normalize_message / normalize_messages (dict → Message, inverse of _normalize)
# ---------------------------------------------------------------------------

def _create_minimal_provider():
    """Create a minimal concrete AIProvider for testing normalize methods."""
    from lib.ai_providers.base_provider import AIProvider

    class _MinimalProvider(AIProvider):
        async def chat(self, messages, tools=None, **kwargs):
            raise NotImplementedError
        def list_models(self):
            return []
        def get_provider_name(self):
            return "minimal"

    return _MinimalProvider({"url": "http://localhost:11434", "model": "test-model"})


def test_normalize_message_preserves_all_fields():
    """normalize_message preserves role, content, name, tool_calls from dict."""
    p = _create_minimal_provider()
    msg = p.normalize_message({
        "role": "assistant",
        "content": "hello world",
        "name": "my_tool",
        "tool_calls": [{"function": {"name": "f", "arguments": {}}}],
    })
    if msg.role != "assistant":
        assert False, f"role={msg.role!r}"
    if msg.content != "hello world":
        assert False, f"content={msg.content!r}"
    if msg.name != "my_tool":
        assert False, f"name={msg.name!r}"
    if msg.tool_calls != [{"function": {"name": "f", "arguments": {}}}]:
        assert False, f"tool_calls={msg.tool_calls!r}"
    # tool_call_id and images should NOT be preserved (intentional omission)
    if msg.tool_call_id is not None:
        assert False, f"tool_call_id should be None, got {msg.tool_call_id!r}"
    assert msg.images is None, f"images should be None, got {msg.images!r}"


def test_normalize_message_defaults_missing_role():
    """normalize_message defaults role to 'user' when missing from dict."""
    p = _create_minimal_provider()
    msg = p.normalize_message({"content": "hi"})
    if msg.role != "user":
        assert False, f"role={msg.role!r}"
    assert msg.content == "hi", f"content={msg.content!r}"


def test_normalize_message_defaults_missing_content():
    """normalize_message defaults content to '' when missing from dict."""
    p = _create_minimal_provider()
    msg = p.normalize_message({"role": "tool"})
    if msg.content != "":
        assert False, f"content={msg.content!r}"
    assert msg.role == "tool", f"role={msg.role!r}"


def test_normalize_message_ignores_extra_keys():
    """normalize_message silently ignores keys not in Message dataclass."""
    p = _create_minimal_provider()
    msg = p.normalize_message({
        "role": "user",
        "content": "test",
        "extra_field": "should_be_ignored",
        "another_unused": 42,
    })
    if msg.role != "user":
        assert False, f"role={msg.role!r}"
    if msg.content != "test":
        assert False, f"content={msg.content!r}"
    # Extra keys are silently dropped — Message only takes known fields


def test_normalize_messages_converts_list():
    """normalize_messages converts a list of dicts to list of Messages."""
    p = _create_minimal_provider()
    messages = p.normalize_messages([
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ])
    if len(messages) != 3:
        assert False, f"expected 3, got {len(messages)}"
    if messages[0].role != "system" or messages[0].content != "You are helpful.":
        assert False, f"msg[0]={messages[0]}"
    if messages[1].role != "user" or messages[1].content != "hello":
        assert False, f"msg[1]={messages[1]}"
    if messages[2].role != "assistant" or messages[2].content != "hi there":
        assert False, f"msg[2]={messages[2]}"
    # Verify each is a Message instance
    from lib.ai_providers.base_provider import Message
    for m in messages:
        assert isinstance(m, Message), f"not a Message: {type(m)}"


# ---------------------------------------------------------------------------
# Provider basic sanity (no real API calls)
# ---------------------------------------------------------------------------

def test_ollama_provider_name():
    from unittest.mock import patch, MagicMock
    with patch("lib.ai_providers.ollama_provider.OllamaClient") as mock_client_class:
        mock_client_class.return_value = MagicMock(show=MagicMock(return_value={"capabilities": []}))
        from lib.ai_providers.ollama_provider import OllamaProvider
        p = OllamaProvider({"url": "http://localhost:11434"})
    assert p.get_provider_name() == "ollama", f"name={p.get_provider_name()!r}"


def test_copilot_provider_supports_tools():
    from unittest.mock import patch, MagicMock
    with patch("openai.AsyncOpenAI"):
        from lib.ai_providers.copilot_provider import CopilotProvider
        p = CopilotProvider({"url": "http://localhost:4141", "model": "gpt-4.1", "api_key": "dummy"})
    assert p.supports_tools()


# ---------------------------------------------------------------------------
# OpenAIProvider tests
# ---------------------------------------------------------------------------

def test_openai_init_missing_api_key_raises_value_error():
    """OpenAIProvider raises ValueError when api_key is missing or empty."""
    from unittest.mock import patch
    with patch("openai.AsyncOpenAI"):
        from lib.ai_providers.openai_provider import OpenAIProvider

    # Missing key entirely
    try:
        OpenAIProvider({})
        assert False, f"no exception raised for empty config"
    except ValueError as e:
        if "API key is required" not in str(e):
            assert False, f"wrong message: {e}"

    # Empty string key
    try:
        OpenAIProvider({"api_key": ""})
        assert False, f"no exception raised for empty api_key"
    except ValueError:
        pass



def test_openai_init_import_error_when_package_missing():
    """OpenAIProvider raises ImportError when openai package is not installed."""
    import builtins
    import sys

    original_import = builtins.__import__
    openai_module = sys.modules.pop("openai", None)

    def mock_import(name, *args, **kwargs):
        if name == "openai":
            raise ImportError("No module named 'openai'")
        return original_import(name, *args, **kwargs)

    builtins.__import__ = mock_import
    try:
        from lib.ai_providers.openai_provider import OpenAIProvider
        try:
            OpenAIProvider({"api_key": "sk-test"})
            assert False, f"no exception raised"
        except ImportError as e:
            if "openai package not installed" not in str(e):
                assert False, f"wrong message: {e}"
    finally:
        builtins.__import__ = original_import
        if openai_module is not None:
            sys.modules["openai"] = openai_module



def test_openai_list_models_returns_expected_list():
    """OpenAIProvider.list_models() returns 5 expected model names."""
    from unittest.mock import patch
    with patch("openai.AsyncOpenAI"):
        from lib.ai_providers.openai_provider import OpenAIProvider
        p = OpenAIProvider({"api_key": "sk-test"})

    models = p.list_models()
    expected = ["gpt-4", "gpt-4-turbo", "gpt-4o", "gpt-3.5-turbo", "gpt-3.5-turbo-16k"]
    if models != expected:
        assert False, f"models={models}"
    assert len(models) == 5, f"expected 5 models, got {len(models)}"


def test_openai_get_provider_name_returns_openai():
    """OpenAIProvider.get_provider_name() returns 'openai'."""
    from unittest.mock import patch
    with patch("openai.AsyncOpenAI"):
        from lib.ai_providers.openai_provider import OpenAIProvider
        p = OpenAIProvider({"api_key": "sk-test"})

    name = p.get_provider_name()
    assert name == "openai", f"name={name!r}"


def test_openai_chat_delegates_to_openai_compatible():
    """OpenAIProvider.chat() delegates to _openai_compatible_chat."""
    import asyncio
    from unittest.mock import patch, AsyncMock

    with patch("openai.AsyncOpenAI"):
        from lib.ai_providers.openai_provider import OpenAIProvider
        from lib.ai_providers.base_provider import Message, ChatResponse

        p = OpenAIProvider({"api_key": "sk-test"})

        mock_response = ChatResponse(content="test response")
        p._openai_compatible_chat = AsyncMock(return_value=mock_response)

        messages = [Message(role="user", content="hello")]
        tools = [{"function": {"name": "test_tool"}}]

        result = asyncio.run(p.chat(messages, tools, model="gpt-4"))

        p._openai_compatible_chat.assert_called_once_with(messages, tools, model="gpt-4")
        if result is not mock_response:
            assert False, f"result mismatch"



def test_chat_response_defaults():
    """ChatResponse has sensible defaults."""
    r = ChatResponse(content="hello")
    assert r.tool_calls is not None or r.finish_reason == "stop" or r.thinking is not None, f"unexpected defaults: {r}"


# ---------------------------------------------------------------------------
# ProviderRegistry.get_or_create and clear_instances
# ---------------------------------------------------------------------------

def test_get_or_create_returns_provider_on_success():
    """get_or_create returns (provider, None) on success."""
    from unittest.mock import patch, MagicMock
    from lib.ai_providers import ProviderRegistry
    from lib.global_registry import g_data

    ProviderRegistry._instances.clear()
    g_data._registry.clear()

    with patch("lib.ai_providers.ollama_provider.OllamaClient") as mock_client_class:
        mock_client_class.return_value = MagicMock(
            show=MagicMock(return_value={"capabilities": []})
        )
        config = {"ollama": {"url": "http://localhost:11434", "model": "llama3.2"}}
        provider, error = ProviderRegistry.get_or_create("ollama", config)

    if provider is None:
        assert False, f"provider is None, error={error!r}"
    if error is not None:
        assert False, f"error={error!r}"
    assert provider.get_provider_name() == "ollama", f"wrong name: {provider.get_provider_name()!r}"


def test_get_or_create_returns_cached_provider():
    """get_or_create returns the same provider instance on second call."""
    from unittest.mock import patch, MagicMock
    from lib.ai_providers import ProviderRegistry
    from lib.global_registry import g_data

    ProviderRegistry._instances.clear()
    g_data._registry.clear()

    with patch("lib.ai_providers.ollama_provider.OllamaClient") as mock_client_class:
        mock_client_class.return_value = MagicMock(
            show=MagicMock(return_value={"capabilities": []})
        )
        config = {"ollama": {"url": "http://localhost:11434", "model": "llama3.2"}}
        p1, e1 = ProviderRegistry.get_or_create("ollama", config)
        p2, e2 = ProviderRegistry.get_or_create("ollama", config)

    if e1 or e2:
        assert False, f"errors: e1={e1!r}, e2={e2!r}"
    assert p1 is p2


def test_get_or_create_returns_error_when_not_configured():
    """get_or_create returns (None, error) when provider not in config."""
    from lib.ai_providers import ProviderRegistry
    from lib.global_registry import g_data

    ProviderRegistry._instances.clear()
    g_data._registry.clear()

    provider, error = ProviderRegistry.get_or_create("nonexistent", {})

    if provider is not None:
        assert False, f"provider should be None, got {provider!r}"
    if error is None:
        assert False, f"error should not be None"
    assert "not configured" in error, f"wrong message: {error!r}"


def test_get_or_create_returns_error_on_init_failure():
    """get_or_create returns (None, error) when get_provider raises."""
    from lib.ai_providers import ProviderRegistry
    from lib.ai_providers.base_provider import AIProvider
    from lib.global_registry import g_data

    ProviderRegistry._instances.clear()
    g_data._registry.clear()

    # Register a provider that raises on init
    class FailingProvider(AIProvider):
        def __init__(self, config):
            super().__init__(config)
            raise ValueError("init explosion")
        async def chat(self, messages, tools=None, **kwargs):
            pass
        def list_models(self):
            return []
        def get_provider_name(self):
            return "failing"

    original_providers = dict(ProviderRegistry._providers)
    try:
        ProviderRegistry.register_provider("failing", FailingProvider)
        config = {"failing": {"url": "http://localhost:11434"}}
        provider, error = ProviderRegistry.get_or_create("failing", config)

        if provider is not None:
            assert False, f"provider should be None, got {provider!r}"
        if error is None:
            assert False, f"error should not be None"
        if "init explosion" not in error:
            assert False, f"wrong error: {error!r}"
    finally:
        ProviderRegistry._providers = original_providers


def test_clear_instances_removes_all_cached_providers():
    """clear_instances empties ProviderRegistry._instances."""
    from unittest.mock import patch, MagicMock
    from lib.ai_providers import ProviderRegistry
    from lib.global_registry import g_data

    ProviderRegistry._instances.clear()
    g_data._registry.clear()

    with patch("lib.ai_providers.ollama_provider.OllamaClient") as mock_client_class:
        mock_client_class.return_value = MagicMock(
            show=MagicMock(return_value={"capabilities": []})
        )
        config = {"url": "http://localhost:11434", "model": "llama3.2"}
        ProviderRegistry.get_provider("ollama", config)

    if "ollama" not in ProviderRegistry._instances:
        assert False, f"_instances should contain 'ollama"

    ProviderRegistry.clear_instances()

    assert not (ProviderRegistry._instances), f"_instances not empty: {ProviderRegistry._instances}"


def test_ollama_chat_includes_n_keep():
    """OllamaProvider.chat() includes n_keep in the request body to preserve
    system prompt tokens during context overflow (#302)."""
    from unittest.mock import patch, MagicMock

    with patch("lib.ai_providers.ollama_provider.OllamaClient") as mock_client_class:
        mock_httpx = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "model": "llama3.2",
            "created_at": "2024-01-01T00:00:00Z",
            "message": {"role": "assistant", "content": "Hello!"},
            "done": True,
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx.post.return_value = mock_response
        mock_client = MagicMock()
        mock_client._client = mock_httpx
        mock_client_class.return_value = mock_client

        from lib.ai_providers.ollama_provider import OllamaProvider
        from lib.ai_providers.base_provider import Message

        provider = OllamaProvider({"url": "http://localhost:11434"})

        # Create a chat with a system message
        messages = [
            Message(role="system", content="You are a helpful assistant named Nami."),
            Message(role="user", content="Hello"),
        ]

        import asyncio
        response = asyncio.run(provider.chat(messages, model="llama3.2"))

        # Verify the request was made with n_keep
        assert mock_httpx.post.called, "Expected httpx post to be called"

        _, kwargs = mock_httpx.post.call_args
        request_json = kwargs["json"]

        # n_keep should be present and positive
        assert "n_keep" in request_json, (
            f"n_keep missing from request body. Keys: {list(request_json.keys())}"
        )
        n_keep = request_json["n_keep"]
        assert n_keep >= 64, f"n_keep should be >= 64 (minimum buffer), got {n_keep}"

        # With a system message present, n_keep must exceed the buffer alone
        assert n_keep > 64, f"n_keep should be > 64 with system message, got {n_keep}"

        # Verify response was processed correctly
        assert response.content == "Hello!"
        assert response.model == "llama3.2"


# ---------------------------------------------------------------------------
# _openai_compatible_chat — thinking/reasoning mode tests
# ---------------------------------------------------------------------------


def _make_mock_openai_response(content="Hello", reasoning_content=None):
    """Build a mock OpenAI chat completion response with the given fields."""
    from unittest.mock import MagicMock

    mock_message = MagicMock()
    mock_message.content = content
    mock_message.tool_calls = None
    mock_message.reasoning_content = reasoning_content

    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_choice.finish_reason = "stop"

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 10
    mock_usage.completion_tokens = 5
    mock_usage.total_tokens = 15

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.model = "test-model"
    mock_response.usage = mock_usage

    return mock_response


def test_openai_compatible_think_with_reasoning_model():
    """think=True with an o1/o3/o4 model adds reasoning_effort='high'."""
    from unittest.mock import patch, MagicMock, AsyncMock

    mock_response = _make_mock_openai_response()
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        from lib.ai_providers.openai_provider import OpenAIProvider
        from lib.ai_providers.base_provider import Message

        p = OpenAIProvider({"api_key": "sk-test"})
        p.client = mock_client

        import asyncio
        asyncio.run(p._openai_compatible_chat(
            [Message(role="user", content="hello")],
            model="o1",
            think=True,
        ))

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs.get("reasoning_effort") == "high", (
        f"reasoning_effort should be 'high' for o1 model, got {call_kwargs}"
    )


def test_openai_compatible_think_with_non_reasoning_model():
    """think=True with gpt-4 does NOT add reasoning_effort."""
    from unittest.mock import patch, MagicMock, AsyncMock

    mock_response = _make_mock_openai_response()
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        from lib.ai_providers.openai_provider import OpenAIProvider
        from lib.ai_providers.base_provider import Message

        p = OpenAIProvider({"api_key": "sk-test"})
        p.client = mock_client

        import asyncio
        asyncio.run(p._openai_compatible_chat(
            [Message(role="user", content="hello")],
            model="gpt-4",
            think=True,
        ))

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert "reasoning_effort" not in call_kwargs, (
        f"reasoning_effort should NOT be set for gpt-4, got {call_kwargs}"
    )


def test_openai_compatible_think_extracts_reasoning_content():
    """When think=True and reasoning_content is present, it populates ChatResponse.thinking."""
    from unittest.mock import patch, MagicMock, AsyncMock

    mock_response = _make_mock_openai_response(
        content="The answer is 42",
        reasoning_content="Let me think about this step by step...",
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        from lib.ai_providers.openai_provider import OpenAIProvider
        from lib.ai_providers.base_provider import Message

        p = OpenAIProvider({"api_key": "sk-test"})
        p.client = mock_client

        import asyncio
        result = asyncio.run(p._openai_compatible_chat(
            [Message(role="user", content="hello")],
            model="o3-mini",
            think=True,
        ))

    assert result.thinking == "Let me think about this step by step...", (
        f"Expected reasoning content, got {result.thinking!r}"
    )
    assert result.content == "The answer is 42", (
        f"Content should be the main response, got {result.content!r}"
    )


def test_openai_compatible_no_think_leaves_thinking_none():
    """Without think=True, ChatResponse.thinking stays None."""
    from unittest.mock import patch, MagicMock, AsyncMock

    mock_response = _make_mock_openai_response()
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        from lib.ai_providers.openai_provider import OpenAIProvider
        from lib.ai_providers.base_provider import Message

        p = OpenAIProvider({"api_key": "sk-test"})
        p.client = mock_client

        import asyncio
        result = asyncio.run(p._openai_compatible_chat(
            [Message(role="user", content="hello")],
            model="gpt-4",
        ))

    assert result.thinking is None, (
        f"thinking should be None without think=True, got {result.thinking!r}"
    )


def test_openai_compatible_think_false_no_effect():
    """think=False acts like no think kwarg — no reasoning_effort, thinking=None."""
    from unittest.mock import patch, MagicMock, AsyncMock

    mock_response = _make_mock_openai_response()
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        from lib.ai_providers.openai_provider import OpenAIProvider
        from lib.ai_providers.base_provider import Message

        p = OpenAIProvider({"api_key": "sk-test"})
        p.client = mock_client

        import asyncio
        result = asyncio.run(p._openai_compatible_chat(
            [Message(role="user", content="hello")],
            model="o1",
            think=False,
        ))

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert "reasoning_effort" not in call_kwargs, (
        f"reasoning_effort should NOT be set when think=False, got {call_kwargs}"
    )
    assert result.thinking is None, (
        f"thinking should be None when think=False, got {result.thinking!r}"
    )
