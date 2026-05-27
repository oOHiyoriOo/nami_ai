"""
Adapter Manager — generic outbound messaging interface for chat adapters.

With the WebSocket-based architecture all platform adapters (Discord, WhatsApp,
etc.) are external services. ``AdapterManager`` is a thin routing layer that
delegates outbound sends to :class:`AdapterWebSocketServer`.

Use :meth:`send_conversation` to push a message to a known conversation, or
:meth:`send_dm` to initiate a direct message with a user. Both methods work
identically for any adapter — the adapter name is the only discriminator.
"""
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lib.services.adapter_ws_server import AdapterWebSocketServer

logger = logging.getLogger(__name__)


class AdapterManager:
    """Thin routing layer over :class:`AdapterWebSocketServer`.

    Provides platform-agnostic outbound messaging to all connected adapters.
    The adapter name is the only platform identifier nami_ai needs to know.
    """

    def __init__(self, ws_server: "AdapterWebSocketServer") -> None:
        self._ws_server = ws_server

    # ------------------------------------------------------------------
    # Outbound helpers (used by NotificationPipeline, tools, scheduler)
    # ------------------------------------------------------------------

    async def send_conversation(
        self, adapter_name: str, conversation_id: str, text: str
    ) -> None:
        """Send a proactive message to a known conversation.

        Args:
            adapter_name:    Target adapter (e.g. ``"discord"``, ``"whatsapp"``).
            conversation_id: Opaque conversation identifier for that adapter.
            text:            Text content to send.
        """
        logger.info(
            "[adapter_mgr] send_conversation → %s/%s: %.60s",
            adapter_name, conversation_id, text,
        )
        await self._ws_server.send_conversation(adapter_name, conversation_id, text)

    async def send_dm(
        self, adapter_name: str, user_id: str, text: str
    ) -> None:
        """Initiate a direct message with a user on a connected adapter.

        Args:
            adapter_name: Target adapter (e.g. ``"discord"``, ``"whatsapp"``).
            user_id:      Opaque user identifier for that adapter (no prefix).
            text:         Text content to send.
        """
        logger.info(
            "[adapter_mgr] send_dm → %s/user:%s: %.60s", adapter_name, user_id, text
        )
        await self._ws_server.send_dm(adapter_name, user_id, text)

    @property
    def connected_adapters(self) -> list[str]:
        """Names of all currently connected adapters."""
        return self._ws_server.connected_adapters

    async def on_message_send(self, event: "Any") -> None:
        """Handle a ``message.send`` event by routing it to the correct adapter.

        Expected event data keys:
          - ``adapter``:  Target adapter name.
          - ``recipient``: Conversation ID, or ``"user:<user_id>"`` for a DM.
          - ``content``:  Text to send.

        Args:
            event: EventBus ``message.send`` event.
        """
        data = event.data if hasattr(event, "data") else event
        adapter = data.get("adapter", "").lower().strip()
        recipient = data.get("recipient", "").strip()
        content = data.get("content", "")

        if not adapter or not recipient:
            logger.warning("[adapter_mgr] message.send missing adapter or recipient — ignoring")
            return

        if recipient.startswith("user:"):
            user_id = recipient.removeprefix("user:")
            await self.send_dm(adapter, user_id, content)
        else:
            await self.send_conversation(adapter, recipient, content)
