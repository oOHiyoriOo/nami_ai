"""
Dynamic loader utility - loads modules dynamically from directories.
Generic implementation following DRY principle.
"""
import importlib.util
import logging
import asyncio
from typing import Callable, Any
from pathlib import Path


class DynamicLoader:
    """Generic dynamic module loader."""

    def __init__(self, directory: str, attribute_name: str):
        """
        Initialize dynamic loader.

        Args:
            directory: Directory to load modules from
            attribute_name: Name of the attribute to look for in modules
        """
        self.directory = Path(directory)
        self.attribute_name = attribute_name

    async def load_all(self, filter_fn: Callable[[Any], bool] | None = None,
                       exclude_prefixes: list[str] | None = None) -> list[Any]:
        """
        Load all modules from directory.

        Args:
            filter_fn: Optional filter function to validate loaded items
            exclude_prefixes: Optional list of filename prefixes to skip

        Returns:
            List of loaded items
        """
        if not self.directory.exists():
            logging.warning(f"Directory not found: {self.directory}")
            return []

        _exclude = exclude_prefixes or []
        items = []
        python_files = [
            f for f in self.directory.iterdir()
            if f.suffix == '.py'
            and f.name != '__init__.py'
            and not any(f.name.startswith(p) for p in _exclude)
        ]

        for file_path in python_files:
            try:
                item = await self._load_module(file_path)
                if item and (filter_fn is None or filter_fn(item)):
                    items.append(item)
                    logging.info(f"Loaded {self.attribute_name} from: {file_path.name}")
            except Exception as e:
                logging.error(f"Error loading {file_path.name}: {e}")

        logging.info(f"Loaded {len(items)} {self.attribute_name}(s) from {self.directory}")
        return items

    async def _load_module(self, file_path: Path) -> Any | None:
        """
        Load a single module file.

        Args:
            file_path: Path to Python file

        Returns:
            The loaded attribute or None
        """
        spec = importlib.util.spec_from_file_location(
            f"dynamic.{file_path.stem}",
            file_path
        )

        if not spec or not spec.loader:
            return None

        module = importlib.util.module_from_spec(spec)
        await asyncio.to_thread(spec.loader.exec_module, module)

        if hasattr(module, self.attribute_name):
            return getattr(module, self.attribute_name)

        return None


class ToolLoader(DynamicLoader):
    """Specialized loader for tools."""

    def __init__(self, tools_directory: str = "OllamaTools"):
        """
        Initialize tool loader.

        Args:
            tools_directory: Directory containing tool modules
        """
        super().__init__(tools_directory, "get_tool")

    async def load_tools(self, exclude_prefixes: list[str] | None = None) -> list[dict]:
        """
        Load all tools.

        Args:
            exclude_prefixes: Optional list of filename prefixes to skip

        Returns:
            List of tool definitions (every get_tool() should return list[dict])
        """
        raw_tools = await self.load_all(exclude_prefixes=exclude_prefixes)
        tools = []
        for tool_fn in raw_tools:
            if not callable(tool_fn):
                continue
            result = tool_fn()
            # Backward-compat guard: some older tools returned a single dict.
            if isinstance(result, dict):
                logging.warning(
                    f"get_tool() returned a bare dict — wrapping in list. "
                    f"Update the tool to return list[dict]."
                )
                result = [result]
            if not isinstance(result, list):
                logging.error(
                    f"get_tool() returned unexpected type {type(result).__name__} — skipping."
                )
                continue
            for t in result:
                processed = self._process_tool(t)
                if processed is not None:
                    tools.append(processed)
        return tools

    def _process_tool(self, tool: dict) -> dict | None:
        """
        Process tool definition.

        Args:
            tool: Raw tool definition

        Returns:
            Processed tool definition, or None if the input is invalid.
        """
        if not isinstance(tool, dict):
            logging.warning(
                f"[dynamic_loader] Non-dict item in tool list: "
                f"{type(tool).__name__} = {repr(tool)[:100]} — skipping."
            )
            return None
        if tool.get('type') != 'function':
            return tool

        result = {
            "type": "function",
            "safe": tool.get("safe", False),
            "function": {
                "name": tool['function']['name'],
                "description": tool['function']['description'],
                "parameters": tool['function']['parameters'],
            },
            "func": tool.get('func')
        }
        if "categories" in tool:
            result["categories"] = tool["categories"]
        return result


# Convenience function for backward compatibility
async def load_tools(client=None, exclude_prefixes: list[str] | None = None) -> list[dict]:
    """
    Load tools from OllamaTools directory.

    Args:
        client: Optional client (for backward compatibility)
        exclude_prefixes: Optional list of filename prefixes to skip

    Returns:
        List of tool definitions
    """
    loader = ToolLoader()
    return await loader.load_tools(exclude_prefixes=exclude_prefixes)
