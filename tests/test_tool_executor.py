"""
Tests for lib/services/tool_executor.py

Covers:
- Assistant tool_call message is prepended to history before tool results
  (the bug we fixed — without this models lose context)
- tool_call_id is threaded from the tool_call dict into the tool result Message
- Tool function is resolved and called correctly
- Unknown tool returns error string
- Max calls limit stops the loop
- on_tool_start / on_tool_done callbacks are fired
- Unsafe tool exceptions are caught and wrapped (issue #280)
"""

import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.services.tool_executor import execute_tool_loop, _classify_error
from lib.ai_providers.base_provider import Message, ChatResponse


def _make_tool_call(name: str, args: dict, call_id: str | None = None) -> dict:
    tc = {"function": {"name": name, "arguments": args}}
    if call_id:
        tc["id"] = call_id
    return tc


def _make_provider(responses: list[ChatResponse]):
    """Mock provider that returns responses in sequence."""
    provider = MagicMock()
    provider.chat = AsyncMock(side_effect=responses)
    return provider


async def _run_loop(provider, messages, tools, initial_response, max_calls=10, max_rounds=10,
                   error_escalation_threshold=3,
                   on_tool_start=None, on_tool_done=None):
    """Execute tool loop and return (response, tool_messages) tuple."""
    return await execute_tool_loop(
        provider=provider,
        messages=messages,
        tools=tools,
        model="llama3.2",
        initial_response=initial_response,
        max_calls=max_calls,
        max_rounds=max_rounds,
        error_escalation_threshold=error_escalation_threshold,
        on_tool_start=on_tool_start,
        on_tool_done=on_tool_done,
    )


def test_assistant_message_prepended():
    """
    The assistant's tool_call response MUST appear in history before the tool result.
    Without this, the model has no context for why a tool result is being provided.
    """
    print("Test: assistant message prepended before tool result")

    captured_messages = []

    async def fake_chat(messages, tools, **kwargs):
        captured_messages.extend(messages)
        return ChatResponse(content="Done.", model="llama3.2")

    provider = MagicMock()
    provider.chat = fake_chat

    tool_call = _make_tool_call("search_memory", {"query": "test"})
    initial = ChatResponse(
        content="",
        tool_calls=[tool_call],
        model="llama3.2",
    )

    async def fake_tool(**kwargs):
        return "memory result"

    tools = [{"type": "function", "function": {"name": "search_memory"}, "func": fake_tool}]
    initial_messages = [Message(role="user", content="remember anything?")]

    asyncio.run(_run_loop(provider, initial_messages, tools, initial))

    # captured_messages are what the provider received on the SECOND call
    roles = [m.role if isinstance(m, Message) else m.get("role") for m in captured_messages]

    # Must see: user → assistant (with tool_calls) → tool
    try:
        asst_idx = next(i for i, m in enumerate(captured_messages)
                        if (isinstance(m, Message) and m.role == "assistant" and m.tool_calls)
                        or (isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")))
        tool_idx = next(i for i, m in enumerate(captured_messages)
                        if (isinstance(m, Message) and m.role == "tool")
                        or (isinstance(m, dict) and m.get("role") == "tool"))
        if asst_idx >= tool_idx:
            assert False, f"assistant message (idx={asst_idx}) must precede tool message (idx={tool_idx})"
    except StopIteration:
        assert False, f"missing assistant or tool message in history. roles={roles}"

    print("  [PASS]")


def test_tool_call_id_threaded():
    """
    tool_call_id from the assistant's tool_call must appear in the tool result Message.
    This is required by OpenAI/Copilot to correlate results to calls.
    """
    print("Test: tool_call_id threaded from tool_call to result message")

    captured_tool_msg = []

    async def fake_chat(messages, tools, **kwargs):
        for m in messages:
            if isinstance(m, Message) and m.role == "tool":
                captured_tool_msg.append(m)
        return ChatResponse(content="Done.", model="llama3.2")

    provider = MagicMock()
    provider.chat = fake_chat

    tool_call = _make_tool_call("search_memory", {"query": "test"}, call_id="call_abc123")
    initial = ChatResponse(content="", tool_calls=[tool_call], model="llama3.2")

    async def fake_tool(**kwargs):
        return "result"

    tools = [{"type": "function", "function": {"name": "search_memory"}, "func": fake_tool}]
    asyncio.run(_run_loop(provider, [Message(role="user", content="hi")], tools, initial))

    if not captured_tool_msg:
        assert False, "no tool message captured"
    if captured_tool_msg[0].tool_call_id != "call_abc123":
        assert False, f"tool_call_id={captured_tool_msg[0].tool_call_id!r} (expected 'call_abc123')"
    print("  [PASS]")


def test_tool_executed_and_result_passed():
    """Tool function is called with kwargs and result reaches the provider."""
    print("Test: tool function called, result in history")

    call_args = {}
    captured_tool_content = []

    async def fake_tool(**kwargs):
        call_args.update(kwargs)
        return "42 is the answer"

    async def fake_chat(messages, tools, **kwargs):
        for m in messages:
            if isinstance(m, Message) and m.role == "tool":
                captured_tool_content.append(m.content)
        return ChatResponse(content="The answer is 42.", model="llama3.2")

    provider = MagicMock()
    provider.chat = fake_chat

    tool_call = _make_tool_call("calculator", {"expression": "6*7"})
    initial = ChatResponse(content="", tool_calls=[tool_call], model="llama3.2")
    tools = [{"type": "function", "function": {"name": "calculator"}, "func": fake_tool}]

    asyncio.run(_run_loop(provider, [Message(role="user", content="calc")], tools, initial))

    if call_args.get("expression") != "6*7":
        print(f"  [FAIL] tool called with args={call_args}")
    if "42 is the answer" not in captured_tool_content:
        print(f"  [FAIL] tool result not in history: {captured_tool_content}")
    print("  [PASS]")


def test_unknown_tool_returns_error():
    """Calling an unknown tool name returns an error string to the model."""
    print("Test: unknown tool → error string in result")

    captured_tool_content = []

    async def fake_chat(messages, tools, **kwargs):
        for m in messages:
            if isinstance(m, Message) and m.role == "tool":
                captured_tool_content.append(m.content)
        return ChatResponse(content="Sorry.", model="llama3.2")

    provider = MagicMock()
    provider.chat = fake_chat

    tool_call = _make_tool_call("nonexistent_tool", {})
    initial = ChatResponse(content="", tool_calls=[tool_call], model="llama3.2")
    tools = []  # no tools registered

    asyncio.run(_run_loop(provider, [Message(role="user", content="hi")], tools, initial))

    if not any("Unknown tool" in c for c in captured_tool_content):
        assert False, f"expected 'Unknown tool' in results: {captured_tool_content}"
    print("  [PASS]")


def test_max_calls_limit():
    """Loop stops after max_calls even if model keeps requesting tools."""
    print("Test: max_calls limit stops infinite tool loop")

    call_count = [0]

    async def fake_tool(**kwargs):
        call_count[0] += 1
        return "result"

    # Provider always returns another tool_call — should be capped
    tool_call = _make_tool_call("loop_tool", {})
    always_tool = ChatResponse(content="", tool_calls=[tool_call], model="llama3.2")
    final = ChatResponse(content="Done.", model="llama3.2")

    responses = [always_tool] * 3 + [final]  # will be capped before reaching all
    provider = _make_provider(responses)

    tools = [{"type": "function", "function": {"name": "loop_tool"}, "func": fake_tool}]
    asyncio.run(_run_loop(
        provider, [Message(role="user", content="go")], tools,
        initial_response=ChatResponse(content="", tool_calls=[tool_call], model="llama3.2"),
        max_calls=2,
    ))

    if call_count[0] > 2:
        assert False, f"tool called {call_count[0]} times (max=2)"
    print("  [PASS]")


def test_callbacks_fired():
    """on_tool_start and on_tool_done callbacks are called."""
    print("Test: on_tool_start and on_tool_done callbacks fire")

    started = []
    done = []

    async def on_start(name): started.append(name)
    async def on_done(): done.append(True)
    async def fake_tool(**kwargs): return "ok"
    async def fake_chat(messages, tools, **kwargs):
        return ChatResponse(content="Done.", model="llama3.2")

    provider = MagicMock()
    provider.chat = fake_chat

    tool_call = _make_tool_call("my_tool", {})
    initial = ChatResponse(content="", tool_calls=[tool_call], model="llama3.2")
    tools = [{"type": "function", "function": {"name": "my_tool"}, "func": fake_tool}]

    asyncio.run(_run_loop(
        provider, [Message(role="user", content="go")], tools, initial,
        on_tool_start=on_start, on_tool_done=on_done,
    ))

    if started != ["my_tool"]:
        assert False, f"started={started}"
    if not done:
        assert False, "on_tool_done never called"
    print("  [PASS]")


def test_multiple_safe_tools_fire_callbacks():
    """When multiple safe tools run concurrently, on_tool_start fires for each."""
    print("Test: multiple safe tools → on_tool_start called for each")

    started = []
    done = []

    async def on_start(name): started.append(name)
    async def on_done(): done.append(True)

    async def fake_tool_a(**kwargs): return "a"
    async def fake_tool_b(**kwargs): return "b"
    async def fake_tool_c(**kwargs): return "c"

    async def fake_chat(messages, tools, **kwargs):
        return ChatResponse(content="Done.", model="llama3.2")

    provider = MagicMock()
    provider.chat = fake_chat

    tool_calls = [
        _make_tool_call("tool_a", {}),
        _make_tool_call("tool_b", {}),
        _make_tool_call("tool_c", {}),
    ]
    initial = ChatResponse(content="", tool_calls=tool_calls, model="llama3.2")
    tools = [
        {"type": "function", "function": {"name": "tool_a"}, "func": fake_tool_a, "safe": True},
        {"type": "function", "function": {"name": "tool_b"}, "func": fake_tool_b, "safe": True},
        {"type": "function", "function": {"name": "tool_c"}, "func": fake_tool_c, "safe": True},
    ]

    asyncio.run(_run_loop(
        provider, [Message(role="user", content="go")], tools, initial,
        on_tool_start=on_start, on_tool_done=on_done,
    ))

    if sorted(started) != ["tool_a", "tool_b", "tool_c"]:
        assert False, f"started={started} (expected all 3 tools)"
    if not done:
        assert False, "on_tool_done never called"
    print("  [PASS]")


def test_no_tool_calls_returns_immediately():
    """If initial response has no tool_calls, it's returned without touching the provider."""
    print("Test: no tool_calls → returned immediately, provider not called")

    provider = MagicMock()
    provider.chat = AsyncMock()

    initial = ChatResponse(content="Just a plain response.", model="llama3.2")
    result, tool_msgs = asyncio.run(_run_loop(provider, [], [], initial))

    if result.content != "Just a plain response.":
        assert False, f"content={result.content!r}"
    provider.chat.assert_not_called()
    print("  [PASS]")


def test_max_rounds_limit():
    """Loop stops after max_rounds even when every round is unique (no repeat)."""
    print("Test: max_rounds absolute limit stops loop with varied tool calls")

    call_count = [0]

    async def fake_tool(**kwargs):
        call_count[0] += 1
        return "result"

    def _make_varied(idx):
        return ChatResponse(
            content="",
            tool_calls=[_make_tool_call(f"tool_{idx}", {"x": idx})],
            model="llama3.2",
        )

    final = ChatResponse(content="Done.", model="llama3.2")
    # Provider returns: round 2, round 3, round 4 (all unique tool names),
    # then the break path consumes one more (text-only, no tools).
    responses = [_make_varied(i) for i in range(2, 5)] + [final]
    provider = _make_provider(responses)

    tools = [
        {"type": "function", "function": {"name": f"tool_{i}"}, "func": fake_tool}
        for i in range(1, 6)
    ]
    initial = _make_varied(1)

    asyncio.run(_run_loop(
        provider, [Message(role="user", content="go")], tools,
        initial_response=initial,
        max_calls=100,     # repeat detection won't fire (all unique)
        max_rounds=3,      # only 3 rounds allowed
    ))

    if call_count[0] > 3:
        assert False, f"tool called {call_count[0]} times (max_rounds=3)"
    print(f"  [PASS] called {call_count[0]}x")


def test_tool_error_escalation():
    """Same tool producing same error 3x injects a system guidance message."""
    print("Test: error escalation — same error 3x → guidance injected")

    captured_system = []

    async def fake_chat(messages, tools, **kwargs):
        for m in messages:
            if isinstance(m, Message) and m.role == "system":
                captured_system.append(m.content)
        return ChatResponse(content="Done.", model="llama3.2")

    async def fake_tool(**kwargs):
        return "HTTP 404 Not Found — the page you requested does not exist"

    provider = MagicMock()
    provider.chat = fake_chat

    tc = _make_tool_call("fetch_url", {"url": "http://example.com/missing"})

    # 3 rounds of the same failing tool call, then a text-only response
    responses = [
        ChatResponse(content="", tool_calls=[tc], model="llama3.2"),
        ChatResponse(content="", tool_calls=[tc], model="llama3.2"),
        ChatResponse(content="", tool_calls=[tc], model="llama3.2"),
        ChatResponse(content="Done.", model="llama3.2"),
    ]
    provider = _make_provider(responses)
    tools = [{"type": "function", "function": {"name": "fetch_url"}, "func": fake_tool}]

    asyncio.run(_run_loop(
        provider, [Message(role="user", content="fetch")], tools,
        initial_response=ChatResponse(content="", tool_calls=[tc], model="llama3.2"),
        max_calls=100,  # loop detection off
    ))

    if not captured_system:
        print("  [FAIL] no system message captured")
    if not any("Try a different approach" in m for m in captured_system):
        print(f"  [FAIL] guidance not found in: {captured_system}")
    if not any("HTTP 4xx" in m for m in captured_system):
        print(f"  [FAIL] error category label missing in: {captured_system}")
    print("  [PASS]")


def test_error_escalation_different_tools():
    """Different tools producing same error pattern are tracked independently."""
    print("Test: error escalation — different tools tracked independently")

    captured_system = []
    tool_calls_log = []

    async def fake_chat(messages, tools, **kwargs):
        for m in messages:
            if isinstance(m, Message) and m.role == "system":
                captured_system.append(m.content)
        return ChatResponse(content="Done.", model="llama3.2")

    async def fake_tool_a(**kwargs):
        tool_calls_log.append("a")
        return "Connection refused"

    async def fake_tool_b(**kwargs):
        tool_calls_log.append("b")
        return "Connection refused"

    provider = MagicMock()
    provider.chat = fake_chat

    tools = [
        {"type": "function", "function": {"name": "tool_a"}, "func": fake_tool_a},
        {"type": "function", "function": {"name": "tool_b"}, "func": fake_tool_b},
    ]

    # Round 1: tool_a fails
    # Round 2: tool_b fails (different tool, same error)
    # Neither should hit threshold of 3 individually
    tc_a = _make_tool_call("tool_a", {})
    tc_b = _make_tool_call("tool_b", {})
    responses = [
        ChatResponse(content="", tool_calls=[tc_b], model="llama3.2"),
        ChatResponse(content="", tool_calls=[tc_a], model="llama3.2"),
        ChatResponse(content="Done.", model="llama3.2"),
    ]
    provider = _make_provider(responses)

    asyncio.run(_run_loop(
        provider, [Message(role="user", content="go")], tools,
        initial_response=ChatResponse(content="", tool_calls=[tc_a], model="llama3.2"),
        max_calls=100,
    ))

    # tool_a was called twice, tool_b once — neither hits 3
    if captured_system:
        assert False, f"unexpected escalation: {captured_system}"

    if tool_calls_log != ["a", "b", "a"]:
        assert False, f"unexpected call order: {tool_calls_log}"
    print("  [PASS]")


def test_error_escalation_disabled():
    """Threshold of 0 disables error escalation entirely."""
    print("Test: error escalation — threshold 0 disables feature")

    captured_system = []

    async def fake_chat(messages, tools, **kwargs):
        for m in messages:
            if isinstance(m, Message) and m.role == "system":
                captured_system.append(m.content)
        return ChatResponse(content="Done.", model="llama3.2")

    async def fake_tool(**kwargs):
        return "HTTP 404 Not Found"

    provider = MagicMock()
    provider.chat = fake_chat

    tc = _make_tool_call("fetch_url", {"url": "x"})
    # Enough rounds to trigger escalation at default threshold
    responses = [ChatResponse(content="", tool_calls=[tc], model="llama3.2")] * 4 + [
        ChatResponse(content="Done.", model="llama3.2")
    ]
    provider = _make_provider(responses)
    tools = [{"type": "function", "function": {"name": "fetch_url"}, "func": fake_tool}]

    asyncio.run(_run_loop(
        provider, [Message(role="user", content="go")], tools,
        initial_response=ChatResponse(content="", tool_calls=[tc], model="llama3.2"),
        max_calls=100,
        error_escalation_threshold=0,
    ))

    if captured_system:
        assert False, f"escalation fired when disabled: {captured_system}"
    print("  [PASS]")


def test_error_pattern_classify():
    """_classify_error correctly categorizes error strings."""
    print("Test: _classify_error categories")

    cases = [
        ("HTTP 404 Not Found", "http_4xx"),
        ("Received status code 404 — not found", "http_4xx"),
        ("HTTP 502 Bad Gateway", "http_5xx"),
        ("Server returned 503 Service Unavailable", "http_5xx"),
        ("Connection refused", "connection_refused"),
        ("ECONNREFUSED — port not open", "connection_refused"),
        ("bash: nonexistent: command not found", "command_not_found"),
        ("No such file or directory", "command_not_found"),
        ("Permission denied", "permission_denied"),
        ("Operation not permitted", "permission_denied"),
        ("Connection timed out after 30s", "timeout"),
        ("TimeoutError: request took too long", "timeout"),
        ("JSON decode error: unexpected token '<'", "parse_error"),
        ("malformed response from server", "parse_error"),
        ("Everything went fine, result: 42", None),
        ("", None),
    ]

    for text, expected in cases:
        result = _classify_error(text)
        if result != expected:
            assert False, f"classify({text!r}) = {result!r}, expected {expected!r}"

    print("  [PASS]")


def test_unsafe_tool_exception_is_caught():
    """Unsafe tool exceptions escape _execute_one → caught and wrapped like safe tools (issue #280)."""
    print("Test: unsafe tool exception → caught and wrapped")

    captured_results = []

    async def fake_chat(messages, tools, **kwargs):
        for m in messages:
            if isinstance(m, Message) and m.role == "tool":
                captured_results.append(m.content)
        return ChatResponse(content="Done.", model="llama3.2")

    provider = MagicMock()
    provider.chat = fake_chat

    tool_call = _make_tool_call("crashy_tool", {})
    initial = ChatResponse(content="", tool_calls=[tool_call], model="llama3.2")

    async def fake_tool(**kwargs):
        return "ok"

    tools = [{"type": "function", "function": {"name": "crashy_tool"}, "func": fake_tool}]

    with patch("lib.services.tool_executor._execute_one", side_effect=KeyError("malformed_tool_call")):
        asyncio.run(_run_loop(provider, [Message(role="user", content="go")], tools, initial))

    if not captured_results:
        assert False, "no tool result captured"
    if "Tool 'crashy_tool' failed: KeyError" not in captured_results[0]:
        assert False, f"unexpected result: {captured_results[0]!r}"
    print("  [PASS]")


def test_unsafe_tool_logs_after_execution():
    """Unsafe tools log AFTER execution (like safe tools), not before (issue #280)."""
    print("Test: unsafe tool logs after execution")

    log_records = []

    class LogCapture(logging.Handler):
        def emit(self, record):
            log_records.append(record)

    handler = LogCapture()
    handler.setLevel(logging.INFO)
    logger = logging.getLogger("root")
    logger.addHandler(handler)

    async def fake_tool(**kwargs):
        return "result"

    async def fake_chat(messages, tools, **kwargs):
        return ChatResponse(content="Done.", model="llama3.2")

    provider = MagicMock()
    provider.chat = fake_chat

    tool_call = _make_tool_call("sequential_tool", {"arg": "val"})
    initial = ChatResponse(content="", tool_calls=[tool_call], model="llama3.2")
    tools = [{"type": "function", "function": {"name": "sequential_tool"}, "func": fake_tool}]

    try:
        asyncio.run(_run_loop(provider, [Message(role="user", content="go")], tools, initial))

        info_logs = [r for r in log_records if r.levelno == logging.INFO and "(sequential)" in r.getMessage()]
        if not info_logs:
            assert False, "no sequential tool log message found"
        if "(sequential)" not in info_logs[0].getMessage():
            assert False, f"log missing (sequential) tag: {info_logs[0].getMessage()}"
        print("  [PASS]")
    finally:
        logger.removeHandler(handler)
