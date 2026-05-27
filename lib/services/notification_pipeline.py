"""
notification_pipeline.py — Standardized proactive message delivery.

Single entry point for all autonomous notifications:
- Routes to configured adapter (any connected WebSocket adapter)
- Falls back to log-only when no adapter is available
- Auto-truncates long messages per configured limit

Config (config.yml):
    notifications:
      adapter: "discord"          # adapter name (must be connected via WS)
      conversation_id: "123"      # conversation to send to
      truncate: true              # auto-truncate long messages
"""

import logging
import time
from dataclasses import dataclass
from typing import Any

from lib.global_registry import g_data

DEFAULT_MAX_CHARS = 10_000


@dataclass
class NotificationResult:
    """Outcome of a notification delivery attempt."""

    delivered: bool
    channel: str
    error: str | None = None
    elapsed_ms: float = 0.0
    truncated: bool = False
    original_length: int = 0
    final_length: int = 0


class NotificationPipeline:
    """Event-driven notification delivery for autonomous system events."""

    def __init__(self, config: Any, event_bus: Any = None) -> None:
        """
        Args:
            config: Full application config (ConfigurationFile instance).
            event_bus: Optional EventBus for subscribing to system events.
        """
        self._config = config
        self._event_bus = event_bus
        notif_cfg = config.data.get("notifications", {})
        self._adapter: str = notif_cfg.get("adapter", "")
        self._conversation_id: str = notif_cfg.get("conversation_id", "")
        self._truncate: bool = notif_cfg.get("truncate", True)
        self._enabled: bool = bool(self._adapter and self._conversation_id)

        if self._enabled:
            logging.info(
                "[notifications] Pipeline initialised — "
                "adapter=%s, conversation_id=%s, truncate=%s",
                self._adapter, self._conversation_id, self._truncate,
            )
        else:
            logging.info(
                "[notifications] No adapter/conversation_id configured — notifications will be log-only"
            )

        if event_bus:
            event_bus.subscribe("task.completed", self._on_task_completed)
            event_bus.subscribe("task.missed", self._on_task_missed)
            # sandbox.job_completed intentionally not subscribed — raw tool output
            # should never be forwarded to Discord; Nami synthesises responses herself.

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def notify(self, message: str, *, source: str = "system") -> NotificationResult:
        """Deliver a notification through the configured adapter.

        Falls back to log-only if no adapter is configured or available.

        Args:
            message: The notification text.
            source:  Origin label (e.g. "task_scheduler", "system_health").
        """
        t0 = time.time()
        original_length = len(message)

        final_message = message
        truncated = False
        if self._truncate and len(message) > DEFAULT_MAX_CHARS:
            final_message = message[: DEFAULT_MAX_CHARS - 3] + "..."
            truncated = True

        if self._enabled:
            result = await self._deliver_via_adapter(final_message, source)
        else:
            result = await self._deliver_log(final_message, source)

        result.elapsed_ms = (time.time() - t0) * 1000
        result.truncated = truncated
        result.original_length = original_length
        result.final_length = len(final_message)
        return result

    # ------------------------------------------------------------------
    # Delivery backends
    # ------------------------------------------------------------------

    async def _deliver_via_adapter(self, message: str, source: str) -> NotificationResult:
        """Send via the configured WebSocket adapter.

        Args:
            message: The notification text.
            source:  Origin label for logging.
        """
        adapter_mgr = g_data.get("adapter_manager")
        if not adapter_mgr:
            logging.warning("[notifications] No adapter_manager — falling back to log for %s", source)
            return await self._deliver_log(message, source)

        try:
            await adapter_mgr.send_conversation(self._adapter, self._conversation_id, message)
            logging.info("[notifications] delivered via adapter=%s (source=%s)", self._adapter, source)
            return NotificationResult(delivered=True, channel=self._adapter)
        except Exception as e:
            logging.error("[notifications] delivery failed (%s): %s", self._adapter, e, exc_info=True)
            return NotificationResult(delivered=False, channel=self._adapter, error=str(e))

    async def _deliver_log(self, message: str, source: str) -> NotificationResult:
        """Fallback: log the notification."""
        logging.info("[notifications:log] [%s] %s", source, message)
        return NotificationResult(delivered=True, channel="log")

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    async def _on_task_completed(self, event: Any) -> None:
        """Route task result back to the originating conversation on task.completed."""
        data = event.data
        result: str = data.get("result", "")
        adapter_name: str = data.get("adapter", "none")
        conversation_id: str = data.get("conversation_id", "")

        if not result or adapter_name == "none":
            return

        adapter_mgr = g_data.get("adapter_manager")
        if adapter_mgr and conversation_id:
            try:
                await adapter_mgr.send_conversation(adapter_name, conversation_id, result)
                return
            except Exception as e:
                logging.error(
                    "[notifications] Failed to route task result to %s:%s: %s",
                    adapter_name, conversation_id, e, exc_info=True,
                )

        # Fallback to global notification channel
        await self.notify(result, source=f"task:{adapter_name}")

    async def _on_task_missed(self, event: Any) -> None:
        """Deliver missed-task notification on task.missed."""
        data = event.data
        notification: str = data.get("notification", "")
        adapter: str = data.get("adapter", "none")

        if not notification or adapter == "none":
            return

        await self.notify(notification, source=f"task:{adapter}")
