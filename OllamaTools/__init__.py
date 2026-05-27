"""
OllamaTools - Tool implementations for AI assistants.
"""
import json
from typing import Any


def tool_error(error: str, **extra_fields) -> str:
    """
    Create a standardized error response for tools.
    
    Args:
        error: Error message
        **extra_fields: Additional context fields (e.g., url, query)
    
    Returns:
        JSON string with consistent error format
    """
    result = {"success": False, "error": error, **extra_fields}
    return json.dumps(result)


def tool_success(data: Any, **extra_fields) -> str:
    """
    Create a standardized success response for tools.
    
    Args:
        data: The result data
        **extra_fields: Additional context fields
    
    Returns:
        JSON string with consistent success format
    """
    result = {"success": True, "data": data, **extra_fields}
    return json.dumps(result)