"""
tool_executor.py — Shared tool call execution loop.

Resolves all tool calls from an AI response by executing each tool and
feeding results back until the model produces a plain-text final response.

Safe tools (read-only, no side effects) are executed concurrently with
asyncio.gather(). Unsafe tools (writes, shell, scheduler) run sequentially.

Used by both the REST API (/api/chat) and the AdapterManager so the loop
lives in exactly one place.
"""

import asyncio
import json
import logging
from collections.abc import Callable, Awaitable

from lib.ai_providers import Message
from lib.services.event_bus import Event
from lib.services.tool_context import _strip_meta
from lib.tool_argument_validator import validate_tool_arguments, ToolArgumentValidationError
from lib.utils.ai_lock import bump_activity


async def execute_tool_loop(
    provider,
    messages: list[Message],
    tools: list[dict],
    model: str,
    initial_response,
    max_calls: int = 10,
    on_tool_start: Callable[[str], Awaitable[None]] | None = None,
    on_tool_done: Callable[[], Awaitable[None]] | None = None,
):
    """
    Execute the tool call loop until the model returns a final text response.

    Safe tools (``"safe": True`` in their schema) within the same round are
    executed concurrently via ``asyncio.gather()``.  Unsafe tools run
    sequentially to avoid interleaved side-effects.

    Loop termination uses a **repetition counter** rather than a total call
    budget.  Each round's tool calls are fingerprinted by tool name + arguments.
    If the same fingerprint repeats ``max_calls`` times in a row the loop is
    considered stuck — the model is re-prompted once without tools so it can
    write a final answer, then the loop exits.  Switching to any different tool
    call resets the counter to zero, so legitimate autonomous multi-step work
    is never artificially capped.

    Args:
        provider:         AI provider instance (must implement .chat()).
        messages:         Current conversation history as Message objects.
        tools:            Full tool list (including 'func' callables).
        model:            Model name string.
        initial_response: First ChatResponse from the provider (may contain tool_calls).
        max_calls:        Max consecutive *identical* rounds before the loop is
                          aborted as stuck.  Recommended: 3–5.
        on_tool_start:    Optional async callback(tool_name) called before each tool runs.
        on_tool_done:     Optional async callback() called after all tools in a round finish.

    Returns:
        Tuple of ``(final_response, tool_messages)`` where ``tool_messages`` is
        a list of ``Message`` objects (role="tool") appended to history during
        the loop.  Callers use this to store tool responses in log storage and
        replace the content with UUID placeholders for persistence.
    """
    sanitized_tools = _strip_meta(tools)
    current_response = initial_response
    current_messages = list(messages)
    call_count = 0          # total individual tool calls — for logging only
    repeat_count = 0        # consecutive rounds with identical fingerprint
    last_round_sig: str = ""
    tool_messages: list[Message] = []

    while current_response.tool_calls:
        # --- Loop detection: fingerprint this round and compare to previous ---
        round_sig = _round_signature(current_response.tool_calls)
        if round_sig == last_round_sig:
            repeat_count += 1
        else:
            repeat_count = 1
            last_round_sig = round_sig

        if repeat_count > max_calls:
            logging.warning(
                f"[tool_executor] Loop detected — identical round repeated "
                f"{repeat_count}x (limit {max_calls}). Re-prompting without tools."
            )
            current_response = await provider.chat(current_messages, None, model=model)
            break

        # Append the assistant's decision (with tool_calls) to history FIRST.
        current_messages.append(Message(
            role="assistant",
            content=current_response.content or "",
            tool_calls=current_response.tool_calls,
        ))

        # Partition this round into safe (parallel) and unsafe (sequential).
        safe_calls = [tc for tc in current_response.tool_calls if _is_safe(tc, tools)]
        unsafe_calls = [tc for tc in current_response.tool_calls if not _is_safe(tc, tools)]

        tool_results: dict[str, str] = {}  # tool_call_id → result text

        # --- Safe tools: run concurrently ---
        if safe_calls:
            bump_activity()  # lock holder is making tool calls — reset inactivity clock
            if on_tool_start:
                for tc in safe_calls:
                    await on_tool_start(tc["function"]["name"])
            results = await asyncio.gather(
                *[_execute_one(tc, tools) for tc in safe_calls],
                return_exceptions=True,
            )
            for tc, result in zip(safe_calls, results):
                bump_activity()  # each completed parallel tool keeps the inactivity clock fresh
                call_count += 1
                tool_results[tc.get("id", tc["function"]["name"])] = (
                    str(result) if not isinstance(result, Exception)
                    else f"Tool '{tc['function']['name']}' failed: {type(result).__name__}"
                )
                if isinstance(result, Exception):
                    logging.error(f"[tool_executor] Safe tool {tc['function']['name']} raised: {result}")
                else:
                    args_info = _format_args(tc)
                    logging.info(f"[tool_executor] [call {call_count}, repeat {repeat_count}/{max_calls}] {tc['function']['name']}{args_info} (parallel)")

        # --- Unsafe tools: run sequentially ---
        for tc in unsafe_calls:
            call_count += 1
            tool_name = tc["function"]["name"]
            bump_activity()  # each sequential tool keeps the inactivity clock fresh
            if on_tool_start:
                await on_tool_start(tool_name)
            logging.info(f"[tool_executor] [call {call_count}, repeat {repeat_count}/{max_calls}] {tool_name}{_format_args(tc)} (sequential)")
            result = await _execute_one(tc, tools)
            tool_results[tc.get("id", tool_name)] = result

        if on_tool_done:
            await on_tool_done()

        # Append all tool results to history in original call order.
        for tc in current_response.tool_calls:
            key = tc.get("id", tc.get("function", {}).get("name", "unknown"))
            msg = Message(
                role="tool",
                content=tool_results.get(key, "No result"),
                tool_call_id=tc.get("id"),
            )
            current_messages.append(msg)
            tool_messages.append(msg)

        current_response = await provider.chat(current_messages, sanitized_tools, model=model)

    # After the loop: replace tool message content with UUID placeholders.
    placeholder_messages: list[Message] = []
    if tool_messages:
        from lib.global_registry import g_data
        tool_log = g_data.get("tool_response_log")
        event_bus = g_data.get("event_bus")
        for msg in tool_messages:
            response_uuid = None
            if tool_log:
                tool_name = msg.tool_call_id or "unknown"
                try:
                    response_uuid = await tool_log.store(
                        tool_name=tool_name,
                        response_text=msg.content,
                        metadata={"tool_call_id": msg.tool_call_id},
                    )
                except Exception as e:
                    logging.error(f"[tool_executor] Failed to store tool response: {e}")
            if response_uuid:
                from lib.services.tool_response_log import make_placeholder
                placeholder_messages.append(Message(
                    role="tool",
                    content=make_placeholder(response_uuid),
                    tool_call_id=msg.tool_call_id,
                ))
                if event_bus:
                    try:
                        await event_bus.publish(Event(
                            type="tool.response_stored",
                            data={
                                "uuid": response_uuid,
                                "tool_name": tool_name,
                                "response_length": len(msg.content),
                            },
                        ))
                    except Exception as e:
                        logging.error(f"[tool_executor] Failed to publish event: {e}")
            else:
                # If storage failed, keep original content (truncated for safety)
                placeholder_messages.append(Message(
                    role="tool",
                    content=msg.content[:500] + ("..." if len(msg.content) > 500 else ""),
                    tool_call_id=msg.tool_call_id,
                ))

    return current_response, placeholder_messages


def _find_tool(tool_name: str, tools: list[dict]) -> dict | None:
    """Return the tool definition dict matching *tool_name*, or None."""
    return next((t for t in tools if t["function"]["name"] == tool_name), None)


async def _execute_one(tool_call: dict, tools: list[dict]) -> str:
    """Execute a single tool call and return the result string."""
    tool_name = tool_call["function"]["name"]
    tool_args = tool_call["function"]["arguments"]

    tool_def = _find_tool(tool_name, tools)

    if not tool_def:
        return f"Unknown tool: {tool_name}"

    tool_fn = tool_def["func"]
    tool_schema = tool_def["function"]

    try:
        validated_args = validate_tool_arguments(tool_schema, tool_args)
        result = await tool_fn(**validated_args)
        return str(result) if not isinstance(result, str) else result
    except ToolArgumentValidationError as e:
        logging.error(f"[tool_executor] Tool {tool_name} argument validation failed: {e}")
        return f"Argument validation error: {e}"
    except Exception as e:
        logging.error(f"[tool_executor] Tool {tool_name} raised: {e}", exc_info=True)
        return f"Tool '{tool_name}' failed: {e}"


def _is_safe(tool_call: dict, tools: list[dict]) -> bool:
    """Return True if the tool is marked safe (read-only, parallelisable)."""
    tool_name = tool_call["function"]["name"]
    tool_def = _find_tool(tool_name, tools)
    return bool(tool_def and tool_def.get("safe", False))


def _round_signature(tool_calls: list[dict]) -> str:
    """Return a stable fingerprint for a set of tool calls.

    Used to detect consecutive identical rounds (loop detection).  Tool calls
    are sorted by name so parallel-order variations don't produce false positives.
    Arguments are normalised to sorted JSON so dict key order doesn't matter.
    """
    parts = []
    for tc in sorted(tool_calls, key=lambda x: x["function"]["name"]):
        name = tc["function"]["name"]
        args = tc["function"].get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (ValueError, TypeError):
                pass
        args_str = json.dumps(args, sort_keys=True) if isinstance(args, dict) else str(args)
        parts.append(f"{name}:{args_str}")
    return "|".join(parts)


def _format_args(tool_call: dict) -> str:
    """Return a compact args string for logging, e.g. `` (command=ls -la)``."""
    raw = tool_call["function"].get("arguments", "")
    if not raw:
        return ""
    try:
        args = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return f" (args={str(raw)[:100]})"
    if not isinstance(args, dict) or not args:
        return ""
    parts = []
    for k, v in args.items():
        parts.append(f"{k}={str(v)[:80]}")
    combined = ", ".join(parts)
    if len(combined) > 120:
        combined = combined[:120] + "..."
    return f" ({combined})"
