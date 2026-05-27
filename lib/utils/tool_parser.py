"""
Tool parsing utilities.

Functions for extracting and parsing tool calls from AI responses.
"""
import re
import json
import logging


def extract_tool_from_xml(response: dict) -> dict:
    """
    Extract tool calls from response content if they're embedded in XML tags.
    
    Handles cases where the model outputs <tool_call>...</tool_call> tags 
    instead of proper structured tool calls.
    
    Args:
        response: Raw response dict with 'message.content' containing potential XML tool calls
        
    Returns:
        Modified response with extracted tool_calls, or original response if no XML found
        
    Raises:
        ValueError: If XML tool call is malformed (invalid JSON, missing fields, etc.)
    """
    content = response.get('message', {}).get('content', '')
    
    tool_call_match = re.search(r'<tool_call>(.*?)</tool_call>', content, re.DOTALL)
    if tool_call_match:
        tool_call_content = tool_call_match.group(1).strip()
        
        # Parse JSON - raise descriptive error if malformed
        try:
            tool_call = json.loads(tool_call_content)
        except json.JSONDecodeError as e:
            error_msg = f"Tool call XML contains invalid JSON: {e}. Content: {tool_call_content[:100]}"
            logging.error(error_msg)
            raise ValueError(error_msg) from e
        
        # Validate required fields
        if not isinstance(tool_call, dict):
            raise ValueError(f"Tool call must be a JSON object, got: {type(tool_call).__name__}")
        
        tool_name = tool_call.get("name")
        if not tool_name:
            raise ValueError(f"Tool call missing 'name' field. Got: {list(tool_call.keys())}")
        
        # Extract arguments (support both 'args' and 'arguments')
        tool_args = tool_call.get("args", tool_call.get("arguments"))
        if tool_args is None:
            raise ValueError(f"Tool call missing 'args' or 'arguments' field. Got: {list(tool_call.keys())}")
        
        if not isinstance(tool_args, dict):
            raise ValueError(f"Tool arguments must be a JSON object, got: {type(tool_args).__name__}")
        
        logging.info(f"Tool call extracted from XML format: {tool_name}")
        
        return {
            "model": response.get("model", "unknown_model"),
            "created_at": response.get('created_at', "unknown_created_at"),
            "message": {
                "role": response.get('message', {}).get("role", "assistant"),
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": tool_name,
                        "arguments": tool_args
                    }
                }]
            }
        }

    return response
