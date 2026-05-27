"""
Ollama AI provider implementation.
"""
import logging
import asyncio
import re
from typing import Any
from ollama import Client as OllamaClient

from .base_provider import AIProvider, Message, ChatResponse


class OllamaProvider(AIProvider):
    """Ollama AI provider."""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize Ollama provider.

        Args:
            config: Configuration dict with 'url' and 'model' keys
        """
        super().__init__(config)
        self.url = config.get('url', 'http://localhost:11434')
        self.default_model = config.get('model')  # None if not set; callers always pass model explicitly
        self.client = OllamaClient(host=self.url)
        # Per-model capability cache — populated lazily on first use
        self._model_capabilities: dict[str, set[str]] = {}
        # Only query capabilities if a default model is configured; otherwise defer to first ensure_capabilities() call
        if self.default_model:
            self.query_model_capabilities()
        else:
            # Permissive fallback — most modern Ollama models support tools and vision.
            # Per-model capabilities are queried lazily via ensure_capabilities() on first use.
            self.capabilities = {"completion", "structured_output", "tools", "vision"}
        logging.info(f"Initialized Ollama provider with URL: {self.url}, model: {self.default_model or '(per-request)'}, capabilities: {self.capabilities}")

    def ensure_capabilities(self, model: str) -> None:
        """
        Ensure capabilities are known for the given model.

        Queries the Ollama API and caches per-model capabilities. Called by
        the pipeline before supports_vision() / supports_tools() checks when
        no default_model is configured, and again on first chat() call for
        any model not yet seen.

        Args:
            model: Model name to query capabilities for
        """
        if not model or model in self._model_capabilities:
            return
        self._model_capabilities[model] = self.query_model_capabilities(model)

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        **kwargs
    ) -> ChatResponse:
        """
        Generate a chat completion using Ollama.
        
        Args:
            messages: List of conversation messages
            tools: Optional list of tools for function calling
            **kwargs: Additional arguments:
                - model: Model name override
                - options: Dict of Ollama options (temperature, top_p, etc.)
                - think: Boolean to enable/disable thinking mode
                - format: JSON schema for structured output (enforces response format)
        """
        model = kwargs.get('model', self.default_model)
        options = kwargs.get('options', {})
        think = kwargs.get('think', None)  # Thinking control parameter
        format_schema = kwargs.get('format', None)  # Structured output schema

        # Lazily query capabilities for this model if not yet known
        self.ensure_capabilities(model)

        # Convert Message objects to Ollama format (no 'name' field)
        ollama_messages = self._normalize_messages(messages, include_name=False)

        # Call Ollama API
        try:
            # Build API call kwargs
            api_kwargs = {
                'model': model,
                'messages': ollama_messages,
                'stream': False
            }
            if tools:
                api_kwargs['tools'] = tools
            if options:
                api_kwargs['options'] = options
            if think is not None:
                api_kwargs['think'] = think
            if format_schema is not None:
                # Ollama supports 'format' parameter for structured output
                api_kwargs['format'] = format_schema
            
            response = await asyncio.to_thread(
                self.client.chat,
                **api_kwargs
            )

            # Extract tool calls if present (handles XML-formatted tool calls)
            # If extraction fails, return error as content so AI can retry
            try:
                response = self._extract_tool_from_xml(response)
            except ValueError as e:
                # Tool call extraction failed - return error to AI for self-correction
                error_message = (
                    f"⚠️ Tool call extraction failed: {str(e)}\n\n"
                    f"Please check your tool call format. It should be:\n"
                    f"<tool_call>{{\n"
                    f'  "name": "tool_name",\n'
                    f'  "arguments": {{"param": "value"}}\n'
                    f"}}</tool_call>\n\n"
                    f"Try again with correct formatting, or respond to the user without tools."
                )
                logging.warning(f"Tool extraction error returned to AI: {str(e)}")
                
                return ChatResponse(
                    content=error_message,
                    tool_calls=None,
                    model=response.get('model', model),
                    finish_reason='error',
                    thinking=None
                )

            message = response.get('message', {})
            tool_calls = message.get('tool_calls')
            content = message.get('content', '')
            thinking = message.get('thinking', '')  # Extract thinking field

            # Strip <think>...</think> from content — Ollama may include it
            # in the content field even when thinking is provided separately
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

            # Validate tool_calls structure if present
            if tool_calls:
                try:
                    validated_tool_calls = self._validate_tool_calls(tool_calls)
                except ValueError as e:
                    # Tool calls are malformed - return error to AI
                    error_message = (
                        f"⚠️ Malformed tool call structure: {str(e)}\n\n"
                        f"Please ensure tool calls follow the correct format:\n"
                        f'{{"function": {{"name": "tool_name", "arguments": {{"param": "value"}}}}}}\n\n'
                        f"Try again with correct formatting, or respond without tools."
                    )
                    logging.warning(f"Tool call validation error returned to AI: {str(e)}")
                    
                    return ChatResponse(
                        content=error_message,
                        tool_calls=None,
                        model=response.get('model', model),
                        finish_reason='error',
                        thinking=None
                    )
                tool_calls = validated_tool_calls

            return ChatResponse(
                content=content,
                tool_calls=tool_calls,
                model=response.get('model', model),
                finish_reason='tool_calls' if tool_calls else 'stop',
                thinking=thinking if thinking else None  # Include thinking in response
            )

        except Exception as e:
            logging.error(f"Ollama chat error: {e}", exc_info=True)
            raise

    def list_models(self) -> list[str]:
        """List available Ollama models.

        Handles both old dict-style responses and new Pydantic ListResponse
        objects returned by ollama-python >= 0.2.
        """
        try:
            response = self.client.list()
            # New ollama-python (>=0.2): ListResponse with .models list of Model objects
            if hasattr(response, 'models'):
                return [getattr(m, 'model', None) or getattr(m, 'name', '') for m in response.models if m]
            # Legacy dict format
            return [m['name'] for m in response.get('models', [])]
        except Exception as e:
            logging.error(f"Failed to list Ollama models: {e}")
            return []

    def get_provider_name(self) -> str:
        """Get provider name."""
        return "ollama"

    @staticmethod
    def _normalize_tool_call(call) -> dict:
        """
        Normalize a tool call to a plain dict.

        The Ollama Python client returns ToolCall Pydantic objects rather than
        raw dicts. Convert them before validation so downstream code always
        works with plain dicts.
        """
        if isinstance(call, dict):
            return call
        # Pydantic model (ollama ToolCall / Function)
        if hasattr(call, 'model_dump'):
            return call.model_dump()
        # Fallback: reconstruct from attributes
        if hasattr(call, 'function'):
            fn = call.function
            return {
                "function": {
                    "name": fn.name if hasattr(fn, 'name') else fn.get('name'),
                    "arguments": fn.arguments if hasattr(fn, 'arguments') else fn.get('arguments', {}),
                }
            }
        try:
            return dict(call)
        except (TypeError, ValueError):
            return call

    def _validate_tool_calls(self, tool_calls: list) -> list[dict]:
        """
        Normalize and validate tool_calls structure.

        Accepts both plain dicts and Ollama ToolCall objects.

        Args:
            tool_calls: Raw tool calls from provider response

        Returns:
            Validated list of plain dicts

        Raises:
            ValueError: If tool_calls structure is invalid after normalization
        """
        if not isinstance(tool_calls, list):
            raise ValueError(f"tool_calls must be a list, got: {type(tool_calls).__name__}")

        validated = []
        for idx, call in enumerate(tool_calls):
            call = self._normalize_tool_call(call)
            if not isinstance(call, dict):
                raise ValueError(f"Tool call #{idx} must be a dict, got: {type(call).__name__}")
            
            # Check for 'function' key
            if 'function' not in call:
                raise ValueError(f"Tool call #{idx} missing 'function' key. Keys: {list(call.keys())}")
            
            function = call['function']
            if not isinstance(function, dict):
                raise ValueError(f"Tool call #{idx} 'function' must be a dict, got: {type(function).__name__}")
            
            # Validate function structure
            if 'name' not in function:
                raise ValueError(f"Tool call #{idx} function missing 'name' key")
            
            if not isinstance(function['name'], str):
                raise ValueError(f"Tool call #{idx} function name must be a string")
            
            if 'arguments' not in function:
                raise ValueError(f"Tool call #{idx} function missing 'arguments' key")
            
            if not isinstance(function['arguments'], dict):
                raise ValueError(f"Tool call #{idx} function arguments must be a dict, got: {type(function['arguments']).__name__}")
            
            validated.append(call)
        
        return validated

    def query_model_capabilities(self, model: str | None = None) -> set[str]:
        """Query the Ollama API for model capabilities and cache them."""
        model = model or self.default_model
        try:
            response = self.client.show(model)
            caps = response.get('capabilities', [])
            caps_set = set(caps)
            
            # Ollama always supports structured output via 'format' parameter
            caps_set.add('structured_output')
            
            # Update both the live capabilities and the per-model cache
            self.capabilities = caps_set
            if model:
                self._model_capabilities[model] = caps_set
            
            logging.info(f"Model '{model}' capabilities: {self.capabilities}")
        except Exception as e:
            logging.warning(f"Failed to query capabilities for model '{model}': {e}")
            self.capabilities = {"completion", "structured_output"}
        return self.capabilities
