"""
AIPipelineHandler subscribes to events and drives the AI pipeline.

Handles:
- ``message.received`` → runs pipeline → publishes ``response.ready``
- ``task.due``         → runs pipeline → publishes ``task.completed``

All incoming messages now arrive from external adapters via the WebSocket
server, which publishes flat ``message.received`` dicts without platform
objects.  This handler is fully platform-agnostic.
"""

import asyncio
import logging
import re
from typing import Any

from lib.global_registry import g_data
from lib.services.ai_pipeline import AIPipelineRequest, ai_pipeline
from lib.services.event_bus import Event, EventBus
from lib.utils.ai_lock import acquire_ai_lock


class AIPipelineHandler:
    """Listens for events and drives the AI pipeline.

    On each incoming message (from any external adapter via WebSocket):
    1. Acquires the **global** pipeline lock (Nami does one thing at a time)
    2. Signals the adapter to start its typing indicator (``message.processing``)
    3. Resolves the default provider
    4. Runs :func:`ai_pipeline.run` with history supplied by the adapter
    5. Publishes ``response.ready`` so the WS server can deliver the reply
    6. Updates the message state cache to ``done`` or ``error``

    On each scheduled task:
    1. Runs :func:`ai_pipeline.run` with a single-message history
    2. Publishes ``task.completed`` with the result
    """

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        # Single global lock — Nami processes one thing at a time (chat, tasks, heartbeat)
        self._global_lock: asyncio.Lock = asyncio.Lock()
        # Expose lock so heartbeat modules can queue behind chat messages naturally
        g_data.register("ai_lock", self._global_lock)
        event_bus.subscribe("message.received", self._on_message_received)
        event_bus.subscribe("task.due", self._on_task_due)

    # ------------------------------------------------------------------
    # Event handler
    # ------------------------------------------------------------------

    async def _on_message_received(self, event: Event) -> None:
        """Handle an incoming message event from any external adapter."""
        data = event.data
        conv_id: str = data.get("conversation_id", "")
        if not await acquire_ai_lock(self._global_lock, label=f"message/{conv_id}"):
            logging.error(
                "[pipeline_handler] Lock holder inactive — dropping message (conv=%s)",
                conv_id,
            )
            return
        try:
            await self._run_pipeline_for_message(data)
        finally:
            self._global_lock.release()

    async def _run_pipeline_for_message(self, data: dict) -> None:
        """Inner pipeline logic — called while holding the conversation lock.

        Supports per-event model and settings overrides so that REST API
        callers can specify a different model or disable personality without
        affecting adapter messages (which always use the configured defaults).

        Args:
            data: Flat event dict from ``message.received`` containing
                  adapter_name, conversation_id, user_id, content, history, and
                  optional overrides: model, enable_memory, enable_personality,
                  think_override, options, provider_tool_schemas.
        """
        adapter_name: str = data.get("adapter_name", "")
        conversation_id: str = data.get("conversation_id", "")

        try:
            cfg = g_data.get("cfg")
            providers_config = cfg.data.get("providers", {}) if cfg else {}

            # Resolve provider — honour per-event model override (e.g. from REST)
            model_ref: str | None = data.get("model")
            if model_ref:
                from lib.utils.model_string import parse_model_string
                try:
                    default_provider, default_model = parse_model_string(model_ref)
                    provider = self._get_provider(default_provider, providers_config)
                    if not provider:
                        raise ValueError(f"provider '{default_provider}' unavailable")
                except (ValueError, Exception) as exc:
                    logging.warning(
                        "[pipeline_handler] Invalid model override '%s' (%s) — falling back to default",
                        model_ref, exc,
                    )
                    model_ref = None

            if not model_ref:
                resolved = self._resolve_provider_config()
                if not resolved:
                    return
                default_provider, default_model, provider = resolved
                model_ref = f"{default_provider}/{default_model}"

            user_id: str = data.get("user_id", "")
            content: str = data.get("content", "")
            history: list = data.get("history", [])

            # Inject adapter-specific capability tools for this conversation
            ws_server = g_data.get("adapter_ws_server")
            adapter_tools = ws_server.get_adapter_tools(adapter_name) if ws_server else []

            # Status callbacks: publish event-bus events so the ws_server can forward
            # liveness pings to the adapter (resets its response timeout).
            async def _on_tool_start(tool_name: str) -> None:
                if adapter_name:
                    await self._event_bus.publish(Event(
                        type="status.update",
                        data={"adapter_name": adapter_name, "conversation_id": conversation_id,
                              "text": f"Using {tool_name}…"},
                    ))

            async def _on_tool_done() -> None:
                if adapter_name:
                    await self._event_bus.publish(Event(
                        type="status.update",
                        data={"adapter_name": adapter_name, "conversation_id": conversation_id,
                              "text": "Thinking…"},
                    ))

            pipeline_request = AIPipelineRequest(
                messages=history,
                user_id=user_id,
                conversation_id=conversation_id,
                image_urls=data.get("image_urls", []),
                display_name=data.get("display_name"),
                channel_name=data.get("channel_name"),
                guild_name=data.get("guild_name"),
                is_dm=data.get("is_dm", False),
                # Per-event overrides (REST API or future adapter extensions)
                enable_memory=data.get("enable_memory", True),
                enable_personality=data.get("enable_personality", True),
                think_override=data.get("think_override"),
                options=data.get("options"),
                provider_tool_schemas=data.get("provider_tool_schemas"),
                additional_tools=adapter_tools or None,
            )

            # Signal adapter to show typing indicator only right before the AI call —
            # not during setup/provider resolution, which avoids false-positive typing.
            if adapter_name and conversation_id:
                await self._event_bus.publish(Event(
                    type="typing.start",
                    data={"adapter_name": adapter_name, "conversation_id": conversation_id},
                ))

            result = await ai_pipeline.run(
                pipeline_request,
                provider=provider,
                model_name=default_model,
                full_model_ref=model_ref,
                user_name=data.get("user_name", user_id),
                original_user_msg=content,
                on_tool_start=_on_tool_start,
                on_tool_done=_on_tool_done,
            )

            # Strip self-prefix if the LLM mirrors the [Nami]: format
            response_content = re.sub(
                r'^\s*\[n?ami\]\s*:\s*', '', result.content, flags=re.IGNORECASE
            )

            await self._event_bus.publish(Event(
                type="response.ready",
                data={
                    "adapter_name": adapter_name,
                    "conversation_id": conversation_id,
                    "content": response_content,
                    "thinking": result.thinking,
                },
            ))

        except Exception:
            logging.error(
                "[pipeline_handler] Error processing message (adapter=%s, conv=%s)",
                adapter_name, conversation_id, exc_info=True,
            )
            # Route error through event bus — ws_server handles cache.set_error and delivery
            await self._event_bus.publish(Event(
                type="response.ready",
                data={
                    "adapter_name": adapter_name,
                    "conversation_id": conversation_id,
                    "content": "I encountered an error processing your message. Please try again.",
                    "error": True,
                },
            ))

    # ------------------------------------------------------------------
    # Scheduled task handler
    # ------------------------------------------------------------------

    async def _on_task_due(self, event: Event) -> None:
        """Handle a scheduled task.due event.

        Acquires the global AI lock before running — Nami does one thing at a time,
        whether that's a chat message, a dream, research, or a scheduled task.
        """
        data = event.data
        task_id: str = data["task_id"]
        prompt: str = data["prompt"]
        user_id: str = data["user_id"]
        conversation_id: str = data["conversation_id"]
        context_messages: int = data.get("context_messages", 10)
        label: str | None = data.get("label")
        adapter = data.get("adapter", "none")

        # Human-readable title: prefer explicit label, fall back to truncated prompt
        task_title: str = label or (prompt[:60] + "…" if len(prompt) > 60 else prompt)

        logging.info(
            f"[pipeline_handler] executing task {task_id!r}: {prompt[:80]}"
        )

        if not await acquire_ai_lock(self._global_lock, label=f"task/{task_id}"):
            logging.error(
                "[pipeline_handler] Lock holder inactive — dropping task (task=%s)",
                task_id,
            )
            return

        try:
            result_text = await self._execute_task_pipeline(
                prompt=prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                context_messages=context_messages,
                adapter=adapter,
            )
            await self._event_bus.publish(Event(
                type="task.completed",
                data={
                    "task_id": task_id,
                    "task_type": "scheduled",
                    "title": task_title,
                    "result": result_text,
                    "success": True,
                    "adapter": adapter,
                    "conversation_id": conversation_id,
                    "recurrence": data.get("recurrence"),
                    "ttl_runs": data.get("ttl_runs"),
                },
            ))
        except Exception as e:
            logging.error(
                f"[pipeline_handler] task {task_id!r} failed: {e}",
                exc_info=True,
            )
            await self._event_bus.publish(Event(
                type="task.completed",
                data={
                    "task_id": task_id,
                    "task_type": "scheduled",
                    "title": task_title,
                    "result": str(e),
                    "success": False,
                    "adapter": adapter,
                    "conversation_id": conversation_id,
                    "recurrence": data.get("recurrence"),
                    "ttl_runs": data.get("ttl_runs"),
                },
            ))
        finally:
            self._global_lock.release()
    async def _execute_task_pipeline(
        self,
        prompt: str,
        user_id: str,
        conversation_id: str,
        context_messages: int,
        adapter: str = "none",
    ) -> str:
        """Run a scheduled task prompt through the AI pipeline."""
        resolved = self._resolve_provider_config()
        if resolved is None:
            raise RuntimeError("Configuration or provider not available")
        default_provider, default_model, provider = resolved

        history = await self._fetch_task_context(
            conversation_id=conversation_id,
            prompt=prompt,
            context_messages=context_messages,
            adapter=adapter,
        )

        request = AIPipelineRequest(
            messages=history,
            user_id=user_id,
            conversation_id=conversation_id,
            enable_memory=True,
            enable_personality=True,
        )

        result = await ai_pipeline.run(
            request,
            provider=provider,
            model_name=default_model,
            full_model_ref=f"{default_provider}/{default_model}",
            user_name=user_id,
            original_user_msg=prompt,
        )
        return result.content

    @staticmethod
    async def _fetch_task_context(
        conversation_id: str,
        prompt: str,
        context_messages: int,
        adapter: str = "none",
    ) -> list[dict[str, str]]:
        """Build minimal history for a scheduled task.

        External adapters own their conversation history; the Python core has
        no access to it for task runs.  We simply wrap the task prompt in a
        single user message so the pipeline has something to act on.

        Args:
            conversation_id: Identifier of the target conversation (unused here,
                             kept for future history-fetch integration).
            prompt:          The task prompt to execute.
            context_messages: Number of history messages requested (currently ignored).
            adapter:         Originating adapter name (informational).

        Returns:
            A single-element list containing the task prompt as a user message.
        """
        logging.debug(
            "[pipeline_handler] task context: adapter=%s conv=%s — "
            "running without history (adapter owns history)",
            adapter, conversation_id,
        )
        return [{"role": "user", "content": prompt}]

    def _resolve_provider_config(self) -> tuple[str, str, Any] | None:
        """Read default provider/model config and return resolved tuple.

        Returns ``(provider_name, model, provider_instance)`` or ``None`` if
        configuration is unavailable or the provider cannot be resolved.
        """
        cfg = g_data.get("cfg")
        if not cfg:
            return None
        providers_config = cfg.data.get("providers", {})
        provider_name = cfg.data.get("default_provider", "ollama")
        model = cfg.data.get("default_model") or "llama3.2"
        provider = self._get_provider(provider_name, providers_config)
        return (provider_name, model, provider) if provider else None

    def _get_provider(self, provider_name: str, providers_config: dict):
        """Get or create a cached provider instance."""
        from lib.ai_providers import ProviderRegistry

        provider, error = ProviderRegistry.get_or_create(provider_name, providers_config)
        if error:
            logging.error(f"[pipeline_handler] {error}")
            return None
        return provider
