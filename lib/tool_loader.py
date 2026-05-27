"""
Tool loader - backward compatible wrapper.
Now uses the generic DynamicLoader utility following DRY principle.
"""
from lib.utils.dynamic_loader import load_tools

# Re-export for backward compatibility
__all__ = ["load_tools"]
