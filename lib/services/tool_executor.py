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
import re
from collections import Counter
from collections.abc import Callable, Awaitable

from lib.ai_providers import Message
from lib.services.event_bus import Event
from lib.services.tool_context import _strip_meta
from lib.tool_argument_validator import validate_tool_arguments, ToolArgumentValidationError
from lib.utils.ai_lock import bump_activity


# ---------------------------------------------------------------------------
# Error pattern classification
# ---------------------------------------------------------------------------

_ERROR_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("connection_refused", re.compile(r"connection\s+refused|ECONNREFUSED|Connection\s+reset", re.IGNORECASE)),
    ("command_not_found", re.compile(r"command\s+not\s+found|Command\s*['\"]?\w+['\"]?\s*not\s+found|No such file or directory", re.IGNORECASE)),
    ("permission_denied", re.compile(r"permission\s+denied|EACCES|not\s+permitted|Operation not permitted", re.IGNORECASE)),
    ("timeout", re.compile(r"time\s*out|timed\s*out|TimeoutError|Connection\s+timed|TimedOut", re.IGNORECASE)),
    ("http_5xx", re.compile(r"HTTP\s+5\d{2}|status\s+code\s+5\d{2}|502\s+Bad\s+Gateway|503\s+Service\s+Unavailable|504\s+Gateway", re.IGNORECASE)),
    ("http_4xx", re.compile(r"HTTP\s+4\d{2}|status\s+code\s+4\d{2}|400\s+Bad\s+Request|401\s+Unauthorized|403\s+Forbidden|404\s+Not\s+Found|405\s+Method|409\s+Conflict|429\s+Too\s+Many", re.IGNORECASE)),
    ("parse_error", re.compile(r"parse\s+error|invalid\s+JSON|unexpected\s+token|SyntaxError|JSON\s+decode|malformed", re.IGNORECASE)),
]

_ERROR_LABELS: dict[str, str] = {
    "http_4xx": "HTTP 4xx (bad request / not found)",
    "http_5xx": "HTTP 5xx (server error)",
    "connection_refused": "connection refused",
    "command_not_found": "command not found",
    "permission_denied": "permission denied",
    "parse_error": "parse error",
    "timeout": "timeout",
}


def _classify_error(result: str) -> str | None:
    """Classify *result* text into an error category or ``None``."""
    if not result:
        return None
    for category, pattern in _ERROR_PATTERNS:
        if pattern.search(result):
            return category
    return None


async def _check_loop_conditions(
    round_count: int,
    repeat_count: int,
    last_round_sig: str,
    current_response,
    max_calls: int,
    max_rounds: int,
    provider,
    current_messages: list[Message],
    model: str,
) -> tuple[bool, int, str, object]:
    """Check loop termination: fingerprinting, repeat counter, round limit.

    Returns ``(should_break, repeat_count, last_round_sig, response)``.
    When *should_break* is True the caller must exit the loop and use
    the returned *response* as the final response.
    """
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
        new_resp = await provider.chat(current_messages, None, model=model)
        return True, repeat_count, last_round_sig, new_resp

    if round_count > max_rounds:
        logging.warning(
            f"[tool_executor] Round limit reached — {round_count} rounds "
            f"(limit {max_rounds}). Re-prompting without tools."
        )
        new_resp = await provider.chat(current_messages, None, model=model)
        return True, repeat_count, last_round_sig, new_resp

    return False, repeat_count, last_round_sig, current_response


def _escalate_errors(
    error_counts: Counter[tuple[str, str]],
    escalated: set[tuple[str, str]],
    error_escalation_threshold: int,
    current_messages: list[Message],
) -> None:
    """Inject guidance system messages for tool/error pairs that exceed threshold."""
    for (tool_name, category), count in error_counts.items():
        if count >= error_escalation_threshold and (tool_name, category) not in escalated:
            escalated.add((tool_name, category))
            label = _ERROR_LABELS.get(category, category)
            guidance = (
                f"Your previous {count} attempts with {tool_name} "
                f"all failed with '{label}'. Try a different approach."
            )
            logging.warning(
                f"[tool_executor] Error escalation: {tool_name} → {category} "
                f"({count}x). Injecting guidance."
            )
            current_messages.append(Message(role="system", content=guidance))


async def _store_tool_responses(
    tool_messages: list[Message],
) -> list[tuple[Message, str | None]]:
    """Store tool responses in ToolResponseLog and publish events.

    Returns a list of ``(msg, uuid_or_none)`` pairs for placeholder construction.
    """
    from lib.global_registry import g_data
    tool_log = g_data.get("tool_response_log")
    event_bus = g_data.get("event_bus")

    results: list[tuple[Message, str | None]] = []
    for msg in tool_messages:
        response_uuid = None
        tool_name = msg.tool_call_id or "unknown"
        if tool_log:
            try:
                response_uuid = await tool_log.store(
                    tool_name=tool_name,
                    response_text=msg.content,
                    metadata={"tool_call_id": msg.tool_call_id},
                )
            except Exception as e:
                logging.error(f"[tool_executor] Failed to store tool response: {e}")
        if response_uuid and event_bus:
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
        results.append((msg, response_uuid))
    return results


def _build_placeholder_messages(
    stored: list[tuple[Message, str | None]],
) -> list[Message]:
    """Build placeholder Message objects from stored tool responses.

    Messages with a UUID get ``[TOOL_RESPONSE:<uuid>]`` content; messages
    whose storage failed keep truncated original content.
    """
    from lib.services.tool_response_log import make_placeholder

    result: list[Message] = []
    for msg, response_uuid in stored:
        if response_uuid:
            result.append(Message(
                role="tool",
                content=make_placeholder(response_uuid),
                tool_call_id=msg.tool_call_id,
            ))
        else:
            result.append(Message(
                role="tool",
                content=msg.content[:500] + ("..." if len(msg.content) > 500 else ""),
                tool_call_id=msg.tool_call_id,
            ))
    return result


async def execute_tool_loop(
    provider,
    messages: list[Message],
    tools: list[dict],
    model: str,
    initial_response,
    max_calls: int = 10,
    max_rounds: int = 10,
    error_escalation_threshold: int = 3,
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
    call resets the counter, so legitimate autonomous multi-step work
    is never artificially capped.

    A total round limit (``max_rounds``) acts as an absolute safety net so the
    loop always terminates even if the model varies arguments every round.

    Error pattern escalation: when the same ``(tool_name, error_category)``
    pair occurs ``error_escalation_threshold`` times within a single loop
    session, a guidance system message is injected to nudge the model toward
    a different approach.  Set to 0 to disable.

    Args:
        provider:         AI provider instance (must implement .chat()).
        messages:         Current conversation history as Message objects.
        tools:            Full tool list (including 'func' callables).
        model:            Model name string.
        initial_response: First ChatResponse from the provider (may contain tool_calls).
        max_calls:        Max consecutive *identical* rounds before the loop is
                          aborted as stuck.  Recommended: 3–5.
        max_rounds:       Absolute max number of tool-call rounds (any pattern).
                          Acts as a safety net.  Default 10.
        error_escalation_threshold:  Error count before injecting guidance.
                                     Default 3.  Set to 0 to disable.
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
    call_count = 0
    round_count = 0
    repeat_count = 0
    last_round_sig: str = ""
    tool_messages: list[Message] = []
    error_counts: Counter[tuple[str, str]] = Counter() if error_escalation_threshold > 0 else None  # type: ignore[valid-type]
    escalated: set[tuple[str, str]] = set()

    while current_response.tool_calls:
        round_count += 1

        should_break, repeat_count, last_round_sig, current_response = await _check_loop_conditions(
            round_count, repeat_count, last_round_sig, current_response,
            max_calls, max_rounds, provider, current_messages, model,
        )
        if should_break:
            break

        current_messages.append(Message(
            role="assistant",
            content=current_response.content or "",
            tool_calls=current_response.tool_calls,
        ))

        safe_calls = [tc for tc in current_response.tool_calls if _is_safe(tc, tools)]
        unsafe_calls = [tc for tc in current_response.tool_calls if not _is_safe(tc, tools)]

        tool_results: dict[str, str] = {}

        # --- Safe tools: run concurrently ---
        if safe_calls:
            bump_activity()
            if on_tool_start:
                for tc in safe_calls:
                    await on_tool_start(tc["function"]["name"])
            results = await asyncio.gather(
                *[_execute_one(tc, tools) for tc in safe_calls],
                return_exceptions=True,
            )
            for tc, result in zip(safe_calls, results):
                bump_activity()
                call_count += 1
                tool_results[tc.get("id", tc["function"]["name"])] = (
                    str(result) if not isinstance(result, Exception)
                    else f"Tool '{tc['function']['name']}' failed: {type(result).__name__}"
                )
                if isinstance(result, Exception):
                    logging.error(f"[tool_executor] Safe tool {tc['function']['name']} raised: {result}")
                else:
                    args_info = _format_args(tc)
                    logging.info(f"[tool_executor] [call {call_count}, round {round_count}/{max_rounds}, repeat {repeat_count}/{max_calls}] {tc['function']['name']}{args_info} (parallel)")

        # --- Unsafe tools: run sequentially ---
        for tc in unsafe_calls:
            call_count += 1
            tool_name = tc["function"]["name"]
            bump_activity()
            if on_tool_start:
                await on_tool_start(tool_name)
            logging.info(f"[tool_executor] [call {call_count}, round {round_count}/{max_rounds}, repeat {repeat_count}/{max_calls}] {tool_name}{_format_args(tc)} (sequential)")
            result = await _execute_one(tc, tools)
            tool_results[tc.get("id", tool_name)] = result

        if on_tool_done:
            await on_tool_done()

        # Append all tool results to history in original call order.
        for tc in current_response.tool_calls:
            key = tc.get("id", tc.get("function", {}).get("name", "unknown"))
            content = tool_results.get(key, "No result")
            msg = Message(role="tool", content=content, tool_call_id=tc.get("id"))
            current_messages.append(msg)
            tool_messages.append(msg)

            if error_counts is not None:
                category = _classify_error(content)
                if category:
                    error_counts[(tc["function"]["name"], category)] += 1

        if error_counts is not None:
            _escalate_errors(error_counts, escalated, error_escalation_threshold, current_messages)

        current_response = await provider.chat(current_messages, sanitized_tools, model=model)

    if tool_messages:
        stored = await _store_tool_responses(tool_messages)
        placeholder_messages = _build_placeholder_messages(stored)
    else:
        placeholder_messages = []

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
