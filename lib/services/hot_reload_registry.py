"""
HotReloadRegistry — maps module paths to selective reload handlers.

Subscribes to ``system.module_changed`` and dispatches to the handler
registered for the given module_path. Supports fnmatch glob patterns
(e.g. ``OllamaTools.*`` matches ``OllamaTools.nami_edit_code``).

Blacklisted modules (event_bus, global_registry, api_server) are
refused with a warning — these require a full process restart.
"""

from __future__ import annotations

import fnmatch
import logging
from typing import Callable

from lib.services.event_bus import EventBus, Event


class HotReloadRegistry:
    """Maps module paths to reload handlers for selective hot-reload.

    Usage::

        hot_reload = HotReloadRegistry(event_bus)
        g_data.register("hot_reload", hot_reload)

        hot_reload.register("lib.services.context_builder", _reload_context_builder)
        hot_reload.register("OllamaTools.*", _reload_single_tool)
    """

    # Modules that must NEVER be hot-reloaded — they would break the running process.
    BLACKLIST: set[str] = {
        "lib.services.event_bus",
        "lib.global_registry",
        "api_server",
        "lib.services.app_initializer",
    }

    def __init__(self, event_bus: EventBus) -> None:
        self._handlers: dict[str, Callable] = {}
        self._event_bus = event_bus
        event_bus.subscribe("system.module_changed", self._on_module_changed)

    def register(self, module_path: str, handler: Callable) -> None:
        """Register a reload handler for a specific module path.

        ``module_path`` may be an exact path (``lib.services.context_builder``)
        or a fnmatch glob (``OllamaTools.*``).  When an event arrives the
        registry tries an exact match first, then falls back to glob matching.
        """
        self._handlers[module_path] = handler

    async def _on_module_changed(self, event: Event) -> None:
        module_path = event.data.get("module_path")

        if not module_path:
            logging.warning("[hot-reload] module_changed event missing module_path")
            return

        # ── Blacklist check ───────────────────────────────────────────
        if module_path in self.BLACKLIST:
            logging.warning(
                "[hot-reload] Refusing to reload blacklisted module: %s "
                "(requires process restart)",
                module_path,
            )
            return

        # ── Find handler (exact match → glob match) ───────────────────
        handler = self._handlers.get(module_path)
        if handler is None:
            for pattern, h in self._handlers.items():
                if fnmatch.fnmatch(module_path, pattern):
                    handler = h
                    break

        if handler is None:
            logging.warning(
                "[hot-reload] No handler registered for module_path=%r "
                "(available: %s)",
                module_path,
                ", ".join(sorted(self._handlers.keys())) or "(none)",
            )
            return

        logging.info("[hot-reload] Reloading module: %s", module_path)
        try:
            await handler(event.data)
        except Exception:
            logging.exception(
                "[hot-reload] Handler for %s raised an exception", module_path
            )
