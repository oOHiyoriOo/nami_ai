"""
ai_pipeline.py — Shared AI request pipeline for all adapters and the REST API.

Encapsulates the full AI interaction flow: context building, vision
preprocessing, thinking mode resolution, provider call, tool execution,
model cache recording, and fire-and-forget memory extraction.

New adapters only need to prepare an ``AIPipelineRequest`` and call
``ai_pipeline.run()``.  Provider resolution is intentionally left to the
caller so that each surface can handle failures appropriately (HTTP exception
for the REST API, log-and-return for chat adapters).
"""

import asyncio
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from lib.ai_providers import Message
from lib.global_registry import g_data
from lib.services.tool_executor import execute_tool_loop
from lib.services.tool_context import _strip_meta
from lib.services.memory_processor import process_memories
from lib.utils.retry import with_retry

# Task-local context injected by the pipeline before tool execution.
# Tools (e.g. schedule_task) read this to get the caller's user_id /
# conversation_id without needing explicit parameter injection.
# ContextVar is asyncio-safe: each task gets its own copy.
pipeline_ctx: ContextVar[dict] = ContextVar("pipeline_ctx", default={})


@dataclass
class AIPipelineRequest:
    """Platform-agnostic input for the AI pipeline."""

    messages: list[dict]
    """Conversation history as dicts with keys: role, content, tool_calls, images."""

    user_id: str | None = None
    """Scoped user ID (e.g. 'discord:123456789') for memory and context."""

    conversation_id: str | None = None
    """Channel or conversation identifier for history scoping."""

    enable_memory: bool = True
    """Inject relevant memories into context and extract new ones after the turn."""

    enable_personality: bool = True
    """Prepend system prompt to context."""

    image_urls: list[str] = field(default_factory=list)
    """Image URLs to attach to the last user message (e.g. from Discord attachments)."""

    think_override: bool | None = None
    """Explicit thinking mode flag. None = auto-detect from trigger words."""

    options: dict[str, Any] | None = None
    """Provider-specific options (temperature, top_p, num_predict, etc.)."""

    provider_tool_schemas: list[dict] | None = None
    """
    Provider-facing tool schemas (without 'func').
    None  = use global tools + adapter tools (default).
    []    = disable tools for this request.
    [...] = use these client-provided schemas (global + adapter tools still executable).
    """

    additional_tools: list[dict] | None = None
    """
    Per-conversation adapter tools (include 'func' closures) injected by the
    pipeline handler from the originating adapter's registered capabilities.
    Merged with global tools for both execution and schema generation.
    """

    display_name: str | None = None
    """Human-readable display name (e.g. Discord display name / nickname)."""

    channel_name: str | None = None
    """Channel name (e.g. '#general', 'DM', etc.)."""

    guild_name: str | None = None
    """Server / group name."""

    is_dm: bool = False
    """Whether this conversation is a direct message."""


@dataclass
class AIPipelineResult:
    """Output from the AI pipeline."""

    content: str
    """AI response text."""

    thinking: str | None = None
    """Internal reasoning (not shown to the user; available for logging/debug)."""

    model_used: str = ""
    """Actual model name used (may differ from default when thinking mode activates)."""

    tool_messages: list[dict] = field(default_factory=list)
    """Tool response messages with ``[TOOL_RESPONSE:uuid]`` placeholders for
    persistence in chat history.  The full responses are stored in
    :class:`ToolResponseLog` and retrievable on demand."""


class AIPipeline:
    """
    Shared AI request pipeline used by all adapters and the REST API.

    Usage::

        result = await ai_pipeline.run(
            AIPipelineRequest(messages=history, user_id="discord:123"),
            provider=my_provider,
            model_name="llama3.2",
            full_model_ref="ollama/llama3.2",
            on_tool_start=lambda name: adapter.set_status(f"Using {name}…"),
            on_tool_done=lambda: adapter.set_status("Thinking…"),
            original_user_msg=message.content,
        )
    """

    async def run(
        self,
        request: AIPipelineRequest,
        provider,
        model_name: str,
        full_model_ref: str = "",
        *,
        on_thinking_mode: Callable[[], Awaitable[None]] | None = None,
        on_tool_start: Callable[[str], Awaitable[None]] | None = None,
        on_tool_done: Callable[[], Awaitable[None]] | None = None,
        user_name: str | None = None,
        original_user_msg: str = "",
        timestamp=None,
    ) -> AIPipelineResult:
        """Execute the full AI pipeline for a single conversation turn.

        Args:
            request:          Platform-agnostic input.
            provider:         Pre-resolved AI provider instance.
            model_name:       Base model name (may be overridden by thinking mode).
            full_model_ref:   Full "provider/model" string for model cache recording.
            on_thinking_mode: Optional async callback fired when thinking mode activates.
            on_tool_start:    Optional async callback(tool_name) before each tool call.
            on_tool_done:     Optional async callback() after each tool call.
            user_name:        Human-readable display name for memory extraction.
            original_user_msg: Last user message text for memory extraction.
            timestamp:        Message timestamp for memory storage (defaults to now).

        Returns:
            AIPipelineResult with content, optional thinking, and the model used.
        """
        enhanced = await self._build_context(request, user_name)
        provider_messages = self._to_provider_messages(enhanced, request.image_urls)
        provider_messages = await self._preprocess_vision(provider_messages, provider, model_name)
        provider_tools, raw_tools = self._resolve_tools(request)
        use_thinking, active_model = await self._resolve_thinking(
            model_name, original_user_msg, request, on_thinking_mode,
        )
        response = await self._call_provider(
            provider, provider_messages, provider_tools, active_model, use_thinking, request,
        )
        response, tool_placeholders = await self._execute_tools(
            response, provider, provider_messages, raw_tools, active_model, request,
            on_tool_start, on_tool_done,
        )
        self._record_model_cache(full_model_ref)
        self._broadcast_activity()
        self._extract_memories(
            response, request, original_user_msg, user_name, bool(tool_placeholders), timestamp,
        )
        return AIPipelineResult(
            content=response.content or "",
            thinking=response.thinking,
            model_used=active_model,
            tool_messages=tool_placeholders,
        )

    async def _build_context(
        self, request: AIPipelineRequest, user_name: str | None,
    ) -> list[dict]:
        """Build enriched message context with personality and memories."""
        context_builder = g_data.get("context_builder")
        return await context_builder.build_context(
            messages=request.messages,
            user_id=request.user_id,
            conversation_id=request.conversation_id,
            enable_personality=request.enable_personality,
            enable_memory=request.enable_memory,
            display_name=request.display_name,
            channel_name=request.channel_name,
            guild_name=request.guild_name,
            is_dm=request.is_dm,
            user_name=user_name,
        )

    async def _preprocess_vision(
        self, provider_messages: list[Message], provider, model_name: str,
    ) -> list[Message]:
        """Run vision preprocessing if a vision service is registered."""
        vision_service = g_data.get("vision_service")
        if not vision_service:
            return provider_messages
        provider.ensure_capabilities(model_name)
        return await vision_service.preprocess_messages(
            provider_messages, provider.supports_vision(),
        )

    def _resolve_tools(
        self, request: AIPipelineRequest,
    ) -> tuple[list[dict] | None, list]:
        """Resolve global + adapter tools into provider schemas and raw executable tools."""
        raw_tools = list(g_data.get("tools") or [])
        if request.additional_tools:
            raw_tools = raw_tools + request.additional_tools
        if request.provider_tool_schemas is not None:
            provider_tools = request.provider_tool_schemas or None
        else:
            provider_tools = _strip_meta(raw_tools) or None
        return provider_tools, raw_tools

    async def _resolve_thinking(
        self,
        model_name: str,
        original_user_msg: str,
        request: AIPipelineRequest,
        on_thinking_mode: Callable[[], Awaitable[None]] | None,
    ) -> tuple[bool, str]:
        """Determine thinking mode and fire the callback if active."""
        cfg = g_data.get("cfg")
        thinking_cfg = cfg.data.get("thinking", {}) if cfg else {}
        use_thinking, active_model = resolve_thinking_mode(
            content=original_user_msg,
            default_model=model_name,
            thinking_cfg=thinking_cfg,
            override=request.think_override,
        )
        if use_thinking:
            logging.info(f"[pipeline] Thinking mode → '{active_model}'")
            if on_thinking_mode:
                await on_thinking_mode()
        return use_thinking, active_model

    async def _call_provider(
        self,
        provider,
        provider_messages: list[Message],
        provider_tools: list[dict] | None,
        active_model: str,
        use_thinking: bool,
        request: AIPipelineRequest,
    ):
        """Call the AI provider with retry + exponential backoff."""
        chat_kwargs: dict[str, Any] = {"model": active_model}
        if use_thinking:
            chat_kwargs["think"] = True
        if request.options:
            chat_kwargs["options"] = request.options
        cfg = g_data.get("cfg")
        retry_attempts = cfg.data.get("bot", {}).get("retry_max_attempts", 5) if cfg else 5
        return await with_retry(
            lambda: provider.chat(provider_messages, provider_tools, **chat_kwargs),
            max_attempts=retry_attempts,
            label=f"provider.chat({active_model})",
        )

    async def _execute_tools(
        self,
        response,
        provider,
        provider_messages: list[Message],
        raw_tools: list,
        active_model: str,
        request: AIPipelineRequest,
        on_tool_start: Callable[[str], Awaitable[None]] | None,
        on_tool_done: Callable[[], Awaitable[None]] | None,
    ) -> tuple:
        """Run the tool execution loop if the provider returned tool calls."""
        if not response.tool_calls or not raw_tools:
            return response, []
        cfg = g_data.get("cfg")
        bot_cfg = cfg.data.get("bot", {}) if cfg else {}
        max_tool_calls = bot_cfg.get("max_tool_calls", 5)
        max_tool_rounds = bot_cfg.get("max_tool_rounds", 10)
        error_escalation_threshold = bot_cfg.get("tool_error_escalation_threshold", 3)
        _ctx_token = pipeline_ctx.set({
            "user_id": request.user_id or "",
            "conversation_id": request.conversation_id or "",
        })
        try:
            response, tool_msgs = await execute_tool_loop(
                provider=provider,
                messages=provider_messages,
                tools=raw_tools,
                model=active_model,
                initial_response=response,
                max_calls=max_tool_calls,
                max_rounds=max_tool_rounds,
                error_escalation_threshold=error_escalation_threshold,
                on_tool_start=on_tool_start,
                on_tool_done=on_tool_done,
            )
        finally:
            pipeline_ctx.reset(_ctx_token)
        tool_placeholders = [
            {"role": m.role, "content": m.content, "tool_call_id": m.tool_call_id}
            for m in tool_msgs
        ]
        return response, tool_placeholders

    def _record_model_cache(self, full_model_ref: str) -> None:
        """Record successful model usage in the model cache."""
        if not full_model_ref:
            return
        model_cache = g_data.get("model_cache")
        if model_cache:
            model_cache.record_success(full_model_ref)

    def _broadcast_activity(self) -> None:
        """Fire-and-forget broadcast of activity to the event bus."""
        event_bus = g_data.get("event_bus")
        if event_bus:
            from lib.services.event_bus import Event
            asyncio.create_task(event_bus.publish(Event("activity.recorded", {})))

    def _extract_memories(
        self,
        response,
        request: AIPipelineRequest,
        original_user_msg: str,
        user_name: str | None,
        used_tools: bool,
        timestamp,
    ) -> None:
        """Fire-and-forget memory extraction from the completed turn."""
        content = response.content or ""
        if not (request.enable_memory and original_user_msg and content and content != "<ignore>"):
            return
        turn_content = f"User: {original_user_msg}\nAssistant: {content}"
        asyncio.create_task(
            process_memories(
                message_content=turn_content,
                user_id=request.user_id,
                user_name=user_name or request.user_id or "unknown",
                conversation_id=request.conversation_id or "",
                timestamp=timestamp,
                has_tool_calls=used_tools,
            )
        )

    @staticmethod
    def _to_provider_messages(enhanced: list[dict], image_urls: list[str]) -> list[Message]:
        """
        Convert context dicts to provider Message objects.
        Injects ``image_urls`` into the last user message when provided.
        """
        messages = []
        for i, m in enumerate(enhanced):
            is_last_user = i == len(enhanced) - 1 and m["role"] == "user"
            images = image_urls if (is_last_user and image_urls) else (m.get("images") or None)
            messages.append(
                Message(
                    role=m["role"],
                    content=m["content"],
                    tool_calls=m.get("tool_calls"),
                    images=images,
                )
            )
        return messages


def resolve_thinking_mode(
    content: str,
    default_model: str,
    thinking_cfg: dict,
    override: bool | None = None,
) -> tuple[bool, str]:
    """
    Determine whether thinking mode should be active for this request.

    Priority order: explicit override → ``default_enabled`` config flag →
    trigger word detection.

    Args:
        content:       User message content to scan for trigger words.
        default_model: Model to use when thinking is NOT active.
        thinking_cfg:  The ``thinking`` section from config.yml.
        override:      Explicit True/False from the caller; None = auto-detect.

    Returns:
        Tuple of ``(use_thinking, model_name)``.
    """
    thinking_model = thinking_cfg.get("model", default_model)

    if override is True:
        return True, thinking_model
    if override is False:
        return False, default_model
    if thinking_cfg.get("default_enabled", False):
        return True, thinking_model

    trigger_words = thinking_cfg.get("trigger_words", [])
    if any(w.lower() in content.lower() for w in trigger_words):
        return True, thinking_model

    return False, default_model


# Singleton — import and use directly everywhere
ai_pipeline = AIPipeline()
