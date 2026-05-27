"""
tool_context.py — ToolContext: decouples tool loading from the chat request lifecycle.

Provides ToolContext, a standalone callable that bundles tools, provider-safe
schemas, and a name→callable map for a single operation context.

Two factory classmethods:
- for_chat()      — Full tool set (current behaviour, backward compatible)
- for_heartbeat() — Filtered subset based on module-declared categories
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from lib.utils.dynamic_loader import ToolLoader
from lib.global_registry import g_data


@dataclass
class ToolContext:
    """Bundles tools, schemas, and execution context for one operation."""

    tools: list[dict] = field(default_factory=list)
    schemas: list[dict] = field(default_factory=list)
    tool_map: dict[str, Callable] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Factory classmethods
    # ------------------------------------------------------------------

    @classmethod
    async def for_chat(cls, user_id: str = "") -> "ToolContext":
        """Load the full chat tool set (current behaviour, backward compatible).

        Includes local tools loaded via ToolLoader plus MCP tools if configured.
        """
        loader = ToolLoader()
        all_tools = await loader.load_tools()

        # Load MCP tools
        try:
            from lib.utils.mcp_loader import load_mcp_tools
            mcp_tools = await load_mcp_tools()
        except Exception as e:
            logging.debug(f"MCP tools not loaded (for_chat): {e}")
            mcp_tools = []

        all_tools.extend(mcp_tools)
        return cls._from_tools(all_tools)

    @classmethod
    async def for_heartbeat(cls, module_name: str) -> "ToolContext":
        """Load heartbeat-appropriate tools filtered by module-declared categories.

        Reads ``heartbeat.modules.<module_name>.tools`` from config to determine
        which tool categories are permitted.  Also includes dream tools (normally
        excluded by ToolLoader) so memory-grooming and auto-dream can use them.

        Returns an empty ToolContext when no categories are declared (fail-safe).
        """
        cfg = g_data.get("cfg")
        hb_cfg = cfg.data.get("heartbeat", {}) if cfg else {}
        mod_cfg = hb_cfg.get("modules", {}).get(module_name, {})
        allowed = set(mod_cfg.get("tools", []))

        if not allowed:
            logging.warning(
                f"[tool_context] Module '{module_name}' declared no 'tools' "
                f"categories — heartbeat will have no tools."
            )
            return cls._from_tools([])

        # Load standard tools (chat tools)
        loader = ToolLoader()
        all_tools = await loader.load_tools()

        # Load dream tools (excluded from ToolLoader by name prefix)
        try:
            from OllamaTools.dream_tools import get_tool as get_dream_tools
            all_tools.extend(list(get_dream_tools()))
        except Exception as e:
            logging.warning(f"[tool_context] Failed to load dream tools: {e}")

        # Filter by allowed categories
        filtered = [t for t in all_tools if _matches_categories(t, allowed)]

        logging.info(
            f"[tool_context] for_heartbeat({module_name}): "
            f"allowed_categories={sorted(allowed)}, "
            f"total_loaded={len(all_tools)}, matched={len(filtered)}"
        )
        return cls._from_tools(filtered)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _from_tools(tools: list[dict]) -> "ToolContext":
        """Build a ToolContext from a raw tool list."""
        schemas = _strip_meta(tools)
        tool_map = {
            t["function"]["name"]: t["func"]
            for t in tools
            if "func" in t and t.get("func")
        }
        return ToolContext(tools=tools, schemas=schemas, tool_map=tool_map)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _matches_categories(tool: dict, allowed: set[str]) -> bool:
    """Return True when *any* of the tool's categories intersect with the allowed set."""
    cats = set(tool.get("categories", []))
    return bool(cats & allowed)


def _strip_meta(tools: list[dict]) -> list[dict]:
    """Return provider-safe schemas — no 'func', 'safe', or 'categories' keys."""
    return [
        {k: v for k, v in t.items() if k not in ("func", "safe", "categories")}
        for t in tools
    ]
