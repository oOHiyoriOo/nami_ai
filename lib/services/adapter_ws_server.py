"""
adapter_ws_server.py — WebSocket server for external chat adapters.

Each platform adapter (Discord, WhatsApp, etc.) connects as a persistent
WebSocket client. nami_ai is event-driven — it does not pull; adapters push
events, nami pushes responses back on the same connection.

Connection URL:
    ws://host:port/api/ws/adapter?name=<adapter_name>&secret=<bridge_secret>

Inbound events (adapter → nami):
    ``capabilities.register`` — adapter announces its supported actions; nami
                                responds with ``capabilities.ack`` containing
                                the adapter's config block from config.yml.
    ``message.received``     — new user message with history; publish to EventBus
    ``message.query``        — adapter asks for the current state of a conversation;
                               nami responds with ``message.status``
    ``message.recover``      — adapter's inactivity timeout fired; nami either
                               re-delivers a finished response or re-queues with
                               a recovery prompt
    ``action.result``        — result of a previously invoked adapter action
    ``ping``                 — keepalive; respond with ``pong``

Outbound events (nami → adapter):
    ``capabilities.ack``    — sent in response to capabilities.register; carries
                              the adapter's config (bridge_secret stripped)
    ``message.queued``      — immediate ack on message.received; conversation is
                              in the global pipeline queue
    ``message.processing``  — pipeline has started processing this conversation;
                              adapter should start typing indicator now
    ``message.status``      — response to message.query with current state
    ``response.ready``      — pipeline response for a conversation
    ``action.invoke``       — request an adapter-side action (e.g. add_reaction)
    ``send.message``        — proactive message to a known conversation
    ``send.dm``             — proactive DM to a user
    ``status.update``       — pipeline liveness ping (tool call in progress)
    ``pong``                — keepalive reply
"""
import asyncio
import json
import logging
import secrets as secrets_mod
import uuid
from typing import TYPE_CHECKING

from fastapi import WebSocket, WebSocketDisconnect

from lib.global_registry import g_data

if TYPE_CHECKING:
    from lib.services.event_bus import EventBus, Event
    from lib.services.message_state_cache import MessageStateCache

logger = logging.getLogger(__name__)

_ACTION_TIMEOUT_S = 30.0


class AdapterWebSocketServer:
    """Manages persistent WebSocket connections from external adapters.

    One instance per running server, stored in ``g_data["adapter_ws_server"]``.
    Each adapter connects once; the connection is kept alive with ping/pong.

    **Capability system:**
    After connecting, adapters send a ``capabilities.register`` event with a
    list of OpenAI-style tool schemas describing platform-specific actions they
    support (e.g. ``add_reaction``, ``create_thread``).  nami responds with
    ``capabilities.ack`` containing the adapter's config block from
    ``config.yml`` (``bridge_secret`` stripped).

    These registered capabilities become live tools for conversations originating
    from that adapter — prefixed ``<adapter_name>_<action_name>`` so the AI can
    unambiguously call them.  When the AI invokes an adapter action, nami sends
    ``action.invoke`` to the bridge, waits up to 30 s for ``action.result``, and
    returns the result string to the AI tool-call loop.
    """

    def __init__(self, config: dict) -> None:
        self._config = config
        self._connections: dict[str, WebSocket] = {}
        # Per-adapter capability tool dicts (include 'func' closures)
        self._adapter_tools: dict[str, list[dict]] = {}
        # Pending action futures: call_id → Future[dict]
        self._action_futures: dict[str, "asyncio.Future[dict]"] = {}
        # Pending REST futures: conversation_id → Future[dict]
        self._rest_pending: dict[str, "asyncio.Future[dict]"] = {}
        # Message state cache — injected by AppInitializer after init()
        self._msg_cache: "MessageStateCache | None" = None

    def set_message_state_cache(self, cache: "MessageStateCache") -> None:
        """Inject the message state cache after it has been initialised.

        Args:
            cache: Initialised :class:`MessageStateCache` instance.
        """
        self._msg_cache = cache

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def connected_adapters(self) -> list[str]:
        """Names of all currently connected adapters."""
        return list(self._connections.keys())

    # ------------------------------------------------------------------
    # REST bridge — pending-future registry
    # ------------------------------------------------------------------

    def register_pending_rest(self, conversation_id: str) -> "asyncio.Future[dict]":
        """Register an asyncio Future for a REST request awaiting pipeline output.

        The Future is resolved (with the full ``response.ready`` data dict) by
        :meth:`_on_response_ready` when the pipeline finishes.  Call
        :meth:`unregister_pending_rest` in a ``finally`` block to clean up.

        Args:
            conversation_id: Unique ID that will appear in ``response.ready``.

        Returns:
            An awaitable Future that resolves to the pipeline result dict.
        """
        future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        self._rest_pending[conversation_id] = future
        return future

    def unregister_pending_rest(self, conversation_id: str) -> None:
        """Remove a pending REST future (idempotent).

        Args:
            conversation_id: ID passed to :meth:`register_pending_rest`.
        """
        self._rest_pending.pop(conversation_id, None)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def handle_connection(
        self, websocket: WebSocket, adapter_name: str, secret: str
    ) -> None:
        """Accept, authenticate, and manage a single adapter WebSocket session.

        Args:
            websocket:    The incoming FastAPI WebSocket.
            adapter_name: Adapter identifier sent as query parameter ``name``.
            secret:       Bridge secret sent as query parameter ``secret``.
        """
        if not self._authenticate(adapter_name, secret):
            await websocket.close(code=4001, reason="Unauthorized")
            logger.warning(
                "[ws_server] rejected '%s' — bad or missing bridge_secret", adapter_name
            )
            return

        await websocket.accept()

        if adapter_name in self._connections:
            logger.warning(
                "[ws_server] '%s' already connected — replacing old connection",
                adapter_name,
            )

        self._connections[adapter_name] = websocket
        logger.info("[ws_server] adapter '%s' connected", adapter_name)

        event_bus: EventBus | None = g_data.get("event_bus")

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(
                        "[ws_server] invalid JSON from '%s': %.200s", adapter_name, raw
                    )
                    continue

                event_type = event.get("type")

                if event_type == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))

                elif event_type == "message.received":
                    asyncio.create_task(
                        self._on_message_received(adapter_name, event, event_bus)
                    )

                elif event_type == "message.query":
                    asyncio.create_task(
                        self._on_message_query(adapter_name, event)
                    )

                elif event_type == "message.recover":
                    asyncio.create_task(
                        self._on_message_recover(adapter_name, event, event_bus)
                    )

                elif event_type == "capabilities.register":
                    await self._on_capabilities_register(adapter_name, event, websocket)

                elif event_type == "action.result":
                    self._on_action_result(event)

                else:
                    logger.debug(
                        "[ws_server] unknown event '%s' from '%s'", event_type, adapter_name
                    )

        except WebSocketDisconnect:
            logger.info("[ws_server] adapter '%s' disconnected", adapter_name)
        except Exception:
            logger.error(
                "[ws_server] error on '%s' connection", adapter_name, exc_info=True
            )
        finally:
            if self._connections.get(adapter_name) is websocket:
                self._connections.pop(adapter_name, None)

    # ------------------------------------------------------------------
    # Inbound event handlers
    # ------------------------------------------------------------------

    async def _on_message_received(
        self,
        adapter_name: str,
        event: dict,
        event_bus: "EventBus | None",
    ) -> None:
        """Write cache entry, ack with ``message.queued``, then publish to EventBus.

        Args:
            adapter_name: Name of the originating adapter.
            event:        Parsed JSON event dict from the adapter.
            event_bus:    Application EventBus (may be None during startup).
        """
        if not event_bus:
            logger.warning(
                "[ws_server] no EventBus — dropping message from '%s'", adapter_name
            )
            return

        from lib.services.event_bus import Event as _Event

        conversation_id: str = event.get("conversation_id", "")

        # Build the normalised data dict that the pipeline expects
        pipeline_data = {
            "adapter_name": adapter_name,
            "conversation_id": conversation_id,
            "user_id": event.get("user_id", ""),
            "user_name": event.get("user_name", ""),
            "display_name": event.get("display_name", ""),
            "content": event.get("content", ""),
            "is_dm": event.get("is_dm", False),
            "channel_name": event.get("channel_name", ""),
            "guild_name": event.get("guild_name"),
            "image_urls": event.get("image_urls", []),
            "history": event.get("history", []),
        }

        # Persist to cache so reconnecting adapters can query state
        if self._msg_cache and conversation_id:
            try:
                await self._msg_cache.put(conversation_id, adapter_name, pipeline_data)
            except Exception:
                logger.error("[ws_server] cache.put failed for conv=%s", conversation_id, exc_info=True)

        # Immediate ack — adapter transitions from ack-wait to queue-wait
        if conversation_id:
            await self._send(adapter_name, {
                "type": "message.queued",
                "conversation_id": conversation_id,
            })

        await event_bus.publish(_Event(type="message.received", data=pipeline_data))

    async def _on_capabilities_register(
        self, adapter_name: str, event: dict, websocket: WebSocket
    ) -> None:
        """Handle ``capabilities.register``: store adapter tools and send config.

        Builds per-adapter tool dicts with WS-backed async ``func`` closures so
        the AI pipeline can call adapter actions the same way it calls local tools.
        Responds immediately with ``capabilities.ack`` containing the adapter's
        config block (``bridge_secret`` stripped) so the bridge can initialise
        its platform client from central config.

        Args:
            adapter_name: Name of the registering adapter.
            event:        Parsed ``capabilities.register`` event dict.
            websocket:    The adapter's WebSocket connection for the ack reply.
        """
        actions: list[dict] = event.get("data", {}).get("actions", [])
        tools: list[dict] = []

        for action in actions:
            fn_block = action.get("function", {})
            action_name: str = fn_block.get("name", "") or action.get("name", "")
            if not action_name:
                continue

            prefixed_name = f"{adapter_name}_{action_name}"

            # Capture variables explicitly to avoid closure-over-loop-variable bug
            def _make_func(adp: str, act: str):
                async def _invoke(**kwargs) -> str:
                    return await self.invoke_action(adp, act, **kwargs)
                return _invoke

            tool_schema: dict = {
                "type": "function",
                "function": {
                    "name": prefixed_name,
                    "description": fn_block.get("description", action.get("description", "")),
                    "parameters": fn_block.get("parameters", action.get("parameters", {})),
                },
                "func": _make_func(adapter_name, action_name),
            }
            tools.append(tool_schema)

        self._adapter_tools[adapter_name] = tools
        logger.info(
            "[ws_server] '%s' registered %d capabilities: %s",
            adapter_name, len(tools),
            [t["function"]["name"] for t in tools],
        )

        # Build config response — strip bridge_secret before sending
        adapter_cfg = dict(self._config.get("adapters", {}).get(adapter_name, {}))
        adapter_cfg.pop("bridge_secret", None)

        await websocket.send_text(json.dumps({
            "type": "capabilities.ack",
            "adapter_name": adapter_name,
            "config": adapter_cfg,
        }))
        logger.info("[ws_server] sent capabilities.ack to '%s' (config keys: %s)",
                    adapter_name, list(adapter_cfg.keys()))

    async def _on_message_query(self, adapter_name: str, event: dict) -> None:
        """Respond to ``message.query`` with the current state from the cache.

        Allows adapters to recover after a reconnect by checking whether their
        pending conversation was already handled.

        Args:
            adapter_name: Querying adapter.
            event:        Parsed ``message.query`` event with ``conversation_id``.
        """
        conversation_id: str = event.get("conversation_id", "")
        if not conversation_id:
            return

        state = "unknown"
        response = None

        if self._msg_cache:
            try:
                entry = await self._msg_cache.get(conversation_id)
                if entry:
                    state = entry["state"]
                    response = entry.get("response")
            except Exception:
                logger.error(
                    "[ws_server] cache.get failed for conv=%s", conversation_id, exc_info=True
                )

        await self._send(adapter_name, {
            "type": "message.status",
            "conversation_id": conversation_id,
            "state": state,
            "response": response,
        })

    async def _on_message_recover(
        self,
        adapter_name: str,
        event: dict,
        event_bus: "EventBus | None",
    ) -> None:
        """Handle a recovery request from an adapter whose inactivity timer fired.

        Recovery strategy by cache state:

        - ``done``                 — re-deliver the stored response (adapter missed it).
        - ``processing``/``queued``— re-queue with a recovery prompt injected.
        - ``error``/``unknown``    — send a failure message to the adapter.

        Args:
            adapter_name: Requesting adapter.
            event:        Parsed ``message.recover`` event with ``conversation_id``.
            event_bus:    Application EventBus for re-queuing.
        """
        conversation_id: str = event.get("conversation_id", "")
        if not conversation_id:
            return

        if not self._msg_cache:
            logger.warning(
                "[ws_server] message.recover conv=%s but cache unavailable", conversation_id
            )
            return

        try:
            entry = await self._msg_cache.get(conversation_id)
        except Exception:
            logger.error(
                "[ws_server] cache.get failed during recover conv=%s",
                conversation_id, exc_info=True,
            )
            entry = None

        if not entry:
            logger.warning("[ws_server] recover: no cache entry for conv=%s", conversation_id)
            await self.send_response(
                adapter_name, conversation_id,
                "I lost track of what I was doing — could you send that again?"
            )
            return

        cached_state = entry["state"]
        logger.info("[ws_server] recover conv=%s cached_state=%s", conversation_id, cached_state)

        if cached_state == "done":
            # Response was computed but the adapter missed delivery — re-send it
            await self.send_response(
                adapter_name, conversation_id, entry["response"] or ""
            )

        elif cached_state in ("queued", "processing"):
            if event_bus:
                original = entry["event"]
                recovery_history = list(original.get("history", []))
                recovery_history.append({
                    "role": "user",
                    "content": (
                        "(System: It seems you paused mid-task. "
                        "Please summarise what you have done so far and complete your response.)"
                    ),
                })
                recovery_event = {**original, "history": recovery_history}
                from lib.services.event_bus import Event as _Event
                await event_bus.publish(_Event(type="message.received", data=recovery_event))
                logger.info(
                    "[ws_server] recover: re-queued conv=%s with recovery prompt", conversation_id
                )

        else:
            await self.send_response(
                adapter_name, conversation_id,
                "Something went wrong while I was working on that — could you try again?"
            )

    def _on_action_result(self, event: dict) -> None:
        """Resolve a pending adapter action future on ``action.result``.

        Args:
            event: Parsed ``action.result`` event dict with ``call_id`` and ``data``.
        """
        call_id: str = event.get("call_id", "")
        future = self._action_futures.pop(call_id, None)
        if future and not future.done():
            future.set_result(event.get("data", {}))
        elif not future:
            logger.warning("[ws_server] action.result for unknown call_id '%s'", call_id)

    # ------------------------------------------------------------------
    # Adapter actions — invoke adapter-side capabilities
    # ------------------------------------------------------------------

    async def invoke_action(
        self, adapter_name: str, action_name: str, **kwargs
    ) -> str:
        """Invoke an adapter-registered action and return the result string.

        Sends an ``action.invoke`` event to the adapter bridge and waits up to
        :data:`_ACTION_TIMEOUT_S` seconds for ``action.result``.  Used by the
        WS-backed tool func closures created in :meth:`_on_capabilities_register`.

        Args:
            adapter_name: Target adapter (must be connected).
            action_name:  Action name as registered by the adapter (without prefix).
            **kwargs:     Action parameters validated by the tool executor.

        Returns:
            JSON string with ``{"success": true, ...}`` or ``{"error": "..."}`` payload.
        """
        call_id = str(uuid.uuid4())
        future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        self._action_futures[call_id] = future

        await self._send(adapter_name, {
            "type": "action.invoke",
            "call_id": call_id,
            "action": action_name,
            "params": kwargs,
        })

        try:
            result = await asyncio.wait_for(asyncio.shield(future), timeout=_ACTION_TIMEOUT_S)
        except asyncio.TimeoutError:
            self._action_futures.pop(call_id, None)
            return json.dumps({"error": f"action '{action_name}' on '{adapter_name}' timed out"})

        if "error" in result:
            return json.dumps({"error": result["error"]})
        return json.dumps({"success": True, **result})

    def get_adapter_tools(self, adapter_name: str) -> list[dict]:
        """Return the registered capability tools for an adapter.

        Args:
            adapter_name: Adapter name (e.g. ``"discord"``).

        Returns:
            List of tool dicts (with ``func`` closures) or empty list if the
            adapter has not registered capabilities yet.
        """
        return self._adapter_tools.get(adapter_name, [])

    # ------------------------------------------------------------------
    # EventBus subscriber — route response.ready back to adapter
    # ------------------------------------------------------------------

    async def _on_response_ready(self, event: "Event") -> None:
        """Route a ``response.ready`` pipeline result to its destination.

        If a REST request is waiting on this ``conversation_id`` the Future is
        resolved with the full event data dict (so the REST endpoint can read
        ``content`` and ``thinking``).  Otherwise the result is forwarded to
        the originating adapter over its WebSocket connection.

        If ``error=True`` is present in the event data, the message cache is
        set to the error state instead of done.

        Args:
            event: EventBus ``response.ready`` event with keys
                   ``adapter_name``, ``conversation_id``, ``content``,
                   and optionally ``thinking`` and ``error``.
        """
        data = event.data
        conversation_id: str = data.get("conversation_id", "")
        is_error: bool = data.get("error", False)

        # REST path — resolve the pending future instead of going over WS
        future = self._rest_pending.get(conversation_id)
        if future and not future.done():
            future.set_result(data)
            return

        # WS path — forward to the adapter connection
        adapter_name: str = data.get("adapter_name", "")
        content: str = data.get("content", "")

        # Persist result (or error) so adapters can re-query after reconnect
        if self._msg_cache and conversation_id:
            try:
                if is_error:
                    await self._msg_cache.set_error(conversation_id)
                else:
                    await self._msg_cache.set_done(conversation_id, content)
            except Exception:
                logger.error(
                    "[ws_server] cache.set_%s failed for conv=%s",
                    "error" if is_error else "done", conversation_id, exc_info=True,
                )

        await self.send_response(adapter_name, conversation_id, content)

    async def _on_status_update_event(self, event: "Event") -> None:
        """Forward a ``status.update`` event to the target adapter.

        Args:
            event: Event with keys ``adapter_name``, ``conversation_id``, ``text``.
        """
        data = event.data
        await self.send_status(
            data.get("adapter_name", ""),
            data.get("text", ""),
            data.get("conversation_id", ""),
        )

    async def _on_typing_start_event(self, event: "Event") -> None:
        """Forward a ``typing.start`` event to the target adapter.

        Args:
            event: Event with keys ``adapter_name``, ``conversation_id``.
        """
        data = event.data
        await self.send_message_processing(
            data.get("adapter_name", ""),
            data.get("conversation_id", ""),
        )

    def subscribe_to_event_bus(self, event_bus: "EventBus") -> None:
        """Subscribe to internal events that need forwarding to adapters.

        Call once during application startup after the EventBus is created.

        Args:
            event_bus: The application EventBus instance.
        """
        event_bus.subscribe("response.ready", self._on_response_ready)
        event_bus.subscribe("status.update", self._on_status_update_event)
        event_bus.subscribe("typing.start", self._on_typing_start_event)

    # ------------------------------------------------------------------
    # Outbound helpers
    # ------------------------------------------------------------------

    async def send_response(
        self, adapter_name: str, conversation_id: str, content: str
    ) -> None:
        """Push a ``response.ready`` event to an adapter or resolve a REST future.

        This is also called on pipeline errors so both WS adapters and REST
        callers receive the error message rather than hanging.

        Args:
            adapter_name:    Target adapter name (ignored when resolving REST).
            conversation_id: Opaque conversation identifier.
            content:         AI response text (may be ``<ignore>``).
        """
        # REST path — resolve pending future (e.g. error recovery)
        future = self._rest_pending.get(conversation_id)
        if future and not future.done():
            future.set_result({"content": content, "thinking": None})
            return

        await self._send(adapter_name, {
            "type": "response.ready",
            "conversation_id": conversation_id,
            "content": content,
        })

    async def send_conversation(
        self, adapter_name: str, conversation_id: str, content: str
    ) -> None:
        """Push a proactive message to a known conversation.

        Args:
            adapter_name:    Target adapter (e.g. ``"discord"``, ``"whatsapp"``).
            conversation_id: Opaque conversation identifier for that adapter.
            content:         Text content to send.
        """
        await self._send(adapter_name, {
            "type": "send.message",
            "conversation_id": conversation_id,
            "content": content,
        })

    async def send_dm(
        self, adapter_name: str, user_id: str, content: str
    ) -> None:
        """Push a DM to a user on a connected adapter.

        Args:
            adapter_name: Target adapter.
            user_id:      Opaque user identifier (without the adapter prefix).
            content:      Text content to send.
        """
        await self._send(adapter_name, {
            "type": "send.dm",
            "user_id": user_id,
            "content": content,
        })

    async def send_message_processing(
        self, adapter_name: str, conversation_id: str
    ) -> None:
        """Notify an adapter that its queued message is now being processed.

        The adapter should start its typing indicator upon receiving this event.
        Also updates the message state cache to ``processing``.

        Args:
            adapter_name:    Target adapter.
            conversation_id: Conversation the pipeline just started working on.
        """
        if self._msg_cache and conversation_id:
            try:
                await self._msg_cache.set_processing(conversation_id)
            except Exception:
                logger.error(
                    "[ws_server] cache.set_processing failed conv=%s", conversation_id, exc_info=True
                )

        await self._send(adapter_name, {
            "type": "message.processing",
            "conversation_id": conversation_id,
        })

    async def send_status(
        self, adapter_name: str, status: str, conversation_id: str = ""
    ) -> None:
        """Push an optional status hint to a connected adapter.

        Adapters may use this to show typing indicators or status text.
        The ``conversation_id`` is included so the adapter can use it to reset
        its per-conversation response timeout (treating this as a liveness ping).
        Failures are silently ignored — status updates are best-effort.

        Args:
            adapter_name:    Target adapter.
            status:          Short status string (e.g. ``"Thinking deeply..."``).
            conversation_id: Opaque conversation ID — lets the adapter correlate
                             the status with a pending response future.
        """
        ws = self._connections.get(adapter_name)
        if not ws:
            return
        try:
            await ws.send_text(json.dumps({
                "type": "status.update",
                "status": status,
                "conversation_id": conversation_id,
            }))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _send(self, adapter_name: str, payload: dict) -> None:
        """Send a JSON event to a connected adapter.

        Args:
            adapter_name: Target adapter name.
            payload:      Event dict to serialise and send.
        """
        ws = self._connections.get(adapter_name)
        if not ws:
            logger.warning(
                "[ws_server] cannot send '%s' — adapter '%s' not connected",
                payload.get("type"), adapter_name,
            )
            return

        try:
            await ws.send_text(json.dumps(payload))
        except Exception as e:
            logger.error(
                "[ws_server] send '%s' to '%s' failed: %s",
                payload.get("type"), adapter_name, e,
            )

    def _authenticate(self, adapter_name: str, secret: str) -> bool:
        """Validate adapter name + secret against config.

        Secrets are configured as ``adapters.<name>.bridge_secret``.
        Connection is denied when no secret is configured.

        Args:
            adapter_name: Name of the adapter attempting to connect.
            secret:       Secret provided in the query string.

        Returns:
            ``True`` if the secret matches; ``False`` otherwise.
        """
        cfg_secret: str = (
            self._config.get("adapters", {})
            .get(adapter_name, {})
            .get("bridge_secret", "")
        )
        if not cfg_secret:
            logger.warning(
                "[ws_server] no bridge_secret for adapter '%s' — denying connection",
                adapter_name,
            )
            return False
        return secrets_mod.compare_digest(cfg_secret, secret)
