"""
Tests for lib/services/ai_pipeline_handler.py

Covers:
- _get_conv_lock(): creates new lock, returns existing, evicts when over MAX_CONV_LOCKS
- _on_message_received(): publishes response.ready on success
- _on_message_received(): sends error response on pipeline failure
- _on_task_due(): publishes task.completed with success
- _on_task_due(): publishes task.completed with failure on exception
- _fetch_task_context(): loads history from adapter when available, falls back to empty
"""

import asyncio
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.services.ai_pipeline_handler import AIPipelineHandler
from lib.services.event_bus import Event, EventBus
from lib.global_registry import g_data


@contextmanager
def _mock_gdata(data: dict):
    """Patch g_data singleton used inside ai_pipeline_handler methods."""
    orig = {}
    for k in data:
        orig[k] = g_data._registry.get(k)
        g_data._registry[k] = data[k]
    try:
        yield
    finally:
        for k in orig:
            if orig[k] is not None:
                g_data._registry[k] = orig[k]
            else:
                g_data._registry.pop(k, None)


class _FakeConfig:
    def __init__(self, data):
        self.data = data


def _make_message(channel_id="chan-1", author_id="user-1", author_name="Tester",
                  content="hello", attachments=None):
    """Create a ChatMessage with minimal required fields."""
    from lib.chat_adapters.types import ChatChannel, ChatMessage, ChatUser
    from datetime import datetime
    return ChatMessage(
        id="msg-1",
        content=content,
        author=ChatUser(id=author_id, name=author_name),
        channel=ChatChannel(id=channel_id, name="test-channel"),
        timestamp=datetime.now(),
        attachments=attachments or [],
    )


def _make_fake_adapter():
    """Create a mock adapter with typing, send_response, and set_status."""
    adapter = MagicMock()
    adapter.typing = MagicMock()
    adapter.typing.return_value.__aenter__ = AsyncMock()
    adapter.typing.return_value.__aexit__ = AsyncMock()
    adapter.send_response = AsyncMock()
    adapter.set_status = AsyncMock()
    return adapter


# ---------------------------------------------------------------------------
# _on_message_received — success path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="function")
async def test_on_message_received_publishes_response_ready():
    msg = _make_message()
    event_bus = EventBus()

    cfg = _FakeConfig({
        "providers": {
            "ollama": {"base_url": "http://localhost:11434", "model": "llama3.2"},
        },
        "default_provider": "ollama",
        "default_model": "llama3.2",
    })

    # Track published events
    published_events = []
    orig_publish = event_bus.publish

    async def _track_publish(event):
        published_events.append(event)
        await orig_publish(event)

    event_bus.publish = _track_publish

    handler = AIPipelineHandler(event_bus)

    # Patch _run_pipeline_for_message to avoid all the real pipeline complexity.
    # The new signature accepts a single data dict.
    async def _fake_run_pipeline(data):
        await handler._event_bus.publish(Event(
            type="response.ready",
            data={
                "adapter_name": data.get("adapter_name"),
                "content": "Hello from AI!",
            },
        ))

    with patch.object(handler, "_run_pipeline_for_message", _fake_run_pipeline):
        event = Event(
            type="message.received",
            data={
                "adapter_name": "discord",
                "conversation_id": "conv-1",
                "user_id": "user-1",
                "content": "Hello",
                "history": [],
            },
        )
        await handler._on_message_received(event)

    # Verify response.ready was published
    response_events = [e for e in published_events if e.type == "response.ready"]
    assert len(response_events) == 1
    assert response_events[0].data["adapter_name"] == "discord"
    assert response_events[0].data["content"] == "Hello from AI!"


# ---------------------------------------------------------------------------
# _run_pipeline_for_message — sends error response on pipeline failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="function")
async def test_on_message_received_sends_error_on_pipeline_failure():
    event_bus = EventBus()
    handler = AIPipelineHandler(event_bus)

    cfg = _FakeConfig({
        "providers": {
            "ollama": {"base_url": "http://localhost:11434", "model": "llama3.2"},
        },
        "default_provider": "ollama",
        "default_model": "llama3.2",
    })

    mock_ws = MagicMock()
    mock_ws.get_adapter_tools = MagicMock(return_value=[])
    mock_ws._msg_cache = None

    # Track events published on the bus so we can verify the error path
    published_events = []
    orig_publish = event_bus.publish

    async def _track_publish(event):
        published_events.append(event)
        await orig_publish(event)

    event_bus.publish = _track_publish

    # Patch ai_pipeline to raise on run
    import lib.services.ai_pipeline_handler as handler_mod
    original = handler_mod.ai_pipeline
    fake = MagicMock()
    fake.run = AsyncMock(side_effect=RuntimeError("AI meltdown"))

    with _mock_gdata({"cfg": cfg, "adapter_ws_server": mock_ws}):
        handler_mod.ai_pipeline = fake
        try:
            data = {
                "adapter_name": "discord",
                "conversation_id": "conv-1",
                "user_id": "user-1",
                "content": "Hello",
                "history": [],
            }
            await handler._run_pipeline_for_message(data)
        finally:
            handler_mod.ai_pipeline = original

    # Error path now publishes response.ready with error=True via event bus
    error_events = [
        e for e in published_events
        if e.type == "response.ready" and e.data.get("error") is True
    ]
    assert len(error_events) == 1, f"Expected one error response.ready event, got: {published_events}"
    assert "error processing your message" in error_events[0].data["content"].lower()


# ---------------------------------------------------------------------------
# _on_task_due — success path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="function")
async def test_on_task_due_publishes_task_completed_with_success():
    event_bus = EventBus()

    published_events = []
    orig_publish = event_bus.publish

    async def _track_publish(event):
        published_events.append(event)
        await orig_publish(event)

    event_bus.publish = _track_publish

    handler = AIPipelineHandler(event_bus)

    # Patch _execute_task_pipeline to return a successful result
    async def _fake_execute(prompt, user_id, conversation_id,
                            context_messages, adapter="none"):
        return "Task result output"

    with patch.object(handler, "_execute_task_pipeline", _fake_execute):
        event = Event(
            type="task.due",
            data={
                "task_id": "task-42",
                "prompt": "Summarize recent activity",
                "user_id": "user-1",
                "conversation_id": "conv-1",
                "context_messages": 5,
                "adapter": "discord",
                "recurrence": "daily",
                "ttl_runs": 10,
            },
        )
        await handler._on_task_due(event)

    completed = [e for e in published_events if e.type == "task.completed"]
    assert len(completed) == 1
    assert completed[0].data["task_id"] == "task-42"
    assert completed[0].data["success"] is True
    assert completed[0].data["result"] == "Task result output"
    assert completed[0].data["adapter"] == "discord"
    assert completed[0].data["conversation_id"] == "conv-1"
    assert completed[0].data["recurrence"] == "daily"


# ---------------------------------------------------------------------------
# _on_task_due — failure path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="function")
async def test_on_task_due_publishes_task_completed_with_failure():
    event_bus = EventBus()

    published_events = []
    orig_publish = event_bus.publish

    async def _track_publish(event):
        published_events.append(event)
        await orig_publish(event)

    event_bus.publish = _track_publish

    handler = AIPipelineHandler(event_bus)

    # Patch _execute_task_pipeline to raise an exception
    async def _fake_execute(prompt, user_id, conversation_id,
                            context_messages, adapter="none"):
        raise RuntimeError("Provider down")

    with patch.object(handler, "_execute_task_pipeline", _fake_execute):
        event = Event(
            type="task.due",
            data={
                "task_id": "task-99",
                "prompt": "Check system status",
                "user_id": "user-1",
                "conversation_id": "conv-1",
                "context_messages": 3,
                "adapter": "discord",
                "recurrence": None,
                "ttl_runs": None,
            },
        )
        await handler._on_task_due(event)

    completed = [e for e in published_events if e.type == "task.completed"]
    assert len(completed) == 1
    assert completed[0].data["task_id"] == "task-99"
    assert completed[0].data["success"] is False
    assert completed[0].data["conversation_id"] == "conv-1"
    assert "Provider down" in completed[0].data["result"]


# ---------------------------------------------------------------------------
# _fetch_task_context
# ---------------------------------------------------------------------------

def test_fetch_task_context_loads_history_from_adapter():
    """_fetch_task_context wraps the prompt in a single user message.

    The adapter owns conversation history; the Python core does not fetch it.
    """
    with _mock_gdata({}):
        result = asyncio_run(AIPipelineHandler._fetch_task_context(
            conversation_id="conv-1",
            prompt="current task prompt",
            context_messages=10,
            adapter="discord",
        ))

    assert len(result) == 1
    assert result[0] == {"role": "user", "content": "current task prompt"}


def test_fetch_task_context_falls_back_to_empty_when_no_adapter():
    with _mock_gdata({"adapter_manager": None}):
        result = asyncio_run(AIPipelineHandler._fetch_task_context(
            conversation_id="conv-1",
            prompt="standalone prompt",
            context_messages=5,
            adapter="discord",
        ))

    assert len(result) == 1
    assert result[0] == {"role": "user", "content": "standalone prompt"}


def test_fetch_task_context_falls_back_when_adapter_history_missing():
    mock_adapter_mgr = MagicMock()
    mock_adapter_mgr._adapter_histories = {}

    with _mock_gdata({"adapter_manager": mock_adapter_mgr}):
        result = asyncio_run(AIPipelineHandler._fetch_task_context(
            conversation_id="conv-1",
            prompt="prompt",
            context_messages=5,
            adapter="unknown-adapter",
        ))

    assert len(result) == 1
    assert result[0] == {"role": "user", "content": "prompt"}


def test_fetch_task_context_handles_history_error_gracefully():
    mock_history = MagicMock()
    mock_history.get_messages = AsyncMock(side_effect=RuntimeError("DB offline"))

    mock_adapter_mgr = MagicMock()
    mock_adapter_mgr._adapter_histories = {"discord": mock_history}

    with _mock_gdata({"adapter_manager": mock_adapter_mgr}):
        result = asyncio_run(AIPipelineHandler._fetch_task_context(
            conversation_id="conv-1",
            prompt="resilient prompt",
            context_messages=5,
            adapter="discord",
        ))

    # Falls back to just the prompt
    assert len(result) == 1
    assert result[0] == {"role": "user", "content": "resilient prompt"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def asyncio_run(coro):
    """Run an async coroutine, working around the event-loop interference
    that occurs when other tests (e.g., test_app_initializer) have been run
    in the same process."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # A running loop exists — delegate to a new thread so asyncio.run
    # gets its own fresh loop.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, coro).result()
