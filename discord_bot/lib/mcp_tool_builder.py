import logging
import json
import aiohttp
import discord
from functools import partial
from typing import List, Dict, Any, Callable, Coroutine, Union

DEFAULT_HEADERS = {
    'Accept': 'application/json',
}

async def _execute_mcp_server_call(
    client: Union[discord.Client, Any],
    source_user: Union[discord.User, Any],
    mcp_server_details: Dict[str, Any],
    query_topic: str,
    **kwargs: Any
) -> Any:
    """
    Internal async function to make an HTTP request to a configured MCP server.
    Focuses on request construction and happy-path response processing.
    All runtime errors (network, HTTP status, parsing) will propagate.
    """
    tool_name = mcp_server_details.get('tool_name', 'Unknown MCP Tool')
    url = mcp_server_details.get('url')
    
    if not url:
        logging.error(f"MCP Config Error for tool '{tool_name}': URL is missing. Cannot proceed.")
        raise ValueError(f"Misconfiguration: URL missing for MCP tool '{tool_name}'.")

    http_method = mcp_server_details.get('http_method', 'POST').upper()
    
    payload_config = mcp_server_details.get('payload_config', {})
    pass_args_as = payload_config.get('pass_args_as', 'json_body' if http_method in ['POST', 'PUT', 'PATCH'] else 'query_params')
    query_topic_server_key = payload_config.get('query_topic_server_key', 'query')
    additional_args_placement = payload_config.get('additional_args_placement', 'root')
    additional_args_nest_under_key = payload_config.get('additional_args_nest_under_key', 'details')

    response_config = mcp_server_details.get('response_config', {})
    expected_response_type = response_config.get('expected_type', 'json')

    auth_config = mcp_server_details.get('authentication')
    headers = DEFAULT_HEADERS.copy()
    
    if http_method in ['POST', 'PUT', 'PATCH']:
        headers['Content-Type'] = mcp_server_details.get('request_content_type', 'application/json')
    elif 'Content-Type' in headers: # For GET/DELETE, Content-Type in request headers is usually not needed
        del headers['Content-Type']


    if auth_config:
        if auth_config.get('method') == 'bearer_token' and auth_config.get('token'):
            headers['Authorization'] = f"Bearer {auth_config['token']}"
        elif auth_config.get('method') == 'api_key_header' and auth_config.get('header_name') and auth_config.get('token'):
            headers[auth_config['header_name']] = auth_config['token']

    final_payload_or_params: Dict[str, Any] = {}
    final_payload_or_params[query_topic_server_key] = query_topic
    if additional_args_placement == 'nested':
        nest_key = additional_args_nest_under_key if additional_args_nest_under_key else 'details'
        final_payload_or_params[nest_key] = kwargs
    else: # 'root'
        final_payload_or_params.update(kwargs)
    
    logging.info(
        f"Calling MCP tool '{tool_name}' at {url} via {http_method}. "
        f"Headers: {headers}. "
        f"Data to send: {final_payload_or_params}"
    )

    request_kwargs = {"headers": headers}
    if pass_args_as == 'json_body' and http_method in ['POST', 'PUT', 'PATCH']:
        request_kwargs['json'] = final_payload_or_params
    elif pass_args_as == 'query_params':
        request_kwargs['params'] = final_payload_or_params
    elif pass_args_as == 'form_data' and http_method in ['POST', 'PUT', 'PATCH']:
        request_kwargs['data'] = final_payload_or_params
    else:
         if http_method in ['POST', 'PUT', 'PATCH']:
              request_kwargs['json'] = final_payload_or_params

    async with aiohttp.ClientSession() as session:
        async with session.request(http_method, url, **request_kwargs) as response:
            # Let the caller handle HTTP errors by catching ClientResponseError
            response.raise_for_status()

            if expected_response_type == 'json':
                response_data = await response.json()
                logging.info(f"MCP Success (JSON) from '{tool_name}': {response_data}")
                return response_data
            elif expected_response_type == 'text':
                response_text = await response.text()
                logging.info(f"MCP Success (Text) from '{tool_name}': {response_text[:200]}...")
                return response_text # Return raw text
            else: # Includes 'raw' or unspecified
                raw_content = await response.text() # Default to text for "raw"
                logging.info(f"MCP Success (Raw/Text) from '{tool_name}'.")
                return raw_content

# The build_mcp_tools function remains the same as previously defined.
# It loads the JSON config and uses functools.partial with the _execute_mcp_server_call above.
def build_mcp_tools(
    config_file_path: str = "mcp_servers_config.json"
) -> List[Dict[str, Any]]:
    mcp_tools_list: List[Dict[str, Any]] = []
    with open(config_file_path, 'r', encoding='utf-8') as f:
        config_data = json.load(f)

    mcp_server_definitions = config_data.get('mcp_servers', {})
    if not isinstance(mcp_server_definitions, dict):
        logging.error(f"Invalid format: 'mcp_servers' in '{config_file_path}' should be a dictionary. No MCP tools loaded.")
        return mcp_tools_list

    for server_key, server_config in mcp_server_definitions.items():
        tool_name = server_config.get('tool_name')
        description_for_llm = server_config.get('description_for_llm')
        
        if not tool_name or not description_for_llm or not server_config.get('url'):
            logging.warning(f"Skipping MCP server '{server_key}' from '{config_file_path}' due to missing essentials ('tool_name', 'description_for_llm', or 'url').")
            continue

        current_tool_params_properties = {
            "query_topic": {
                "type": "string",
                "description": f"The primary query, specific claim, or topic to send to the '{tool_name}' service."
            }
        }
        current_tool_required = ["query_topic"]

        additional_params_schema = server_config.get('parameters_schema', {})
        if isinstance(additional_params_schema, dict):
            for param_name, schema_def in additional_params_schema.items():
                current_tool_params_properties[param_name] = schema_def
        
        required_additional_params = server_config.get('required_parameters', [])
        if isinstance(required_additional_params, list):
            current_tool_required.extend(required_additional_params)
        current_tool_required = list(set(current_tool_required))

        bound_mcp_call_func = partial(_execute_mcp_server_call, mcp_server_details=server_config.copy())
        
        tool_object = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": description_for_llm,
                "parameters": {
                    "type": "object",
                    "properties": current_tool_params_properties,
                    "required": current_tool_required
                }
            },
            "func": bound_mcp_call_func
        }
        mcp_tools_list.append(tool_object)
        logging.info(f"Successfully built MCP tool structure: '{tool_name}' for server key '{server_key}'.")

    if not mcp_tools_list:
        logging.info(f"No MCP tools were built from '{config_file_path}'. Check file content and format.")
    return mcp_tools_list