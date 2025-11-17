"""
Ollama AI provider implementation.
"""
import logging
import asyncio
import json
import re
from typing import List, Dict, Any, Optional, AsyncIterator
from ollama import Client as OllamaClient

from .base_provider import AIProvider, Message, ChatResponse


class OllamaProvider(AIProvider):
    """Ollama AI provider."""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Ollama provider.

        Args:
            config: Configuration dict with 'url' and 'model' keys
        """
        super().__init__(config)
        self.url = config.get('url', 'http://localhost:11434')
        self.default_model = config.get('model', 'llama2')
        self.client = OllamaClient(host=self.url)
        logging.info(f"Initialized Ollama provider with URL: {self.url}, model: {self.default_model}")

    async def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        **kwargs
    ) -> ChatResponse:
        """Generate a chat completion using Ollama."""
        model = kwargs.get('model', self.default_model)

        # Convert Message objects to Ollama format
        ollama_messages = []
        for msg in messages:
            message_dict = {
                "role": msg.role,
                "content": msg.content
            }
            if msg.tool_calls:
                message_dict["tool_calls"] = msg.tool_calls
            ollama_messages.append(message_dict)

        # Call Ollama API
        try:
            response = await asyncio.to_thread(
                self.client.chat,
                model=model,
                messages=ollama_messages,
                tools=tools,
                stream=False
            )

            # Extract tool calls if present
            response = self._extract_tool_request(response)

            tool_calls = response.get('message', {}).get('tool_calls')
            content = response.get('message', {}).get('content', '')

            return ChatResponse(
                content=content,
                tool_calls=tool_calls,
                model=response.get('model', model),
                finish_reason='tool_calls' if tool_calls else 'stop'
            )

        except Exception as e:
            logging.error(f"Ollama chat error: {e}", exc_info=True)
            raise

    async def chat_stream(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """Generate a streaming chat completion using Ollama."""
        model = kwargs.get('model', self.default_model)

        # Convert Message objects to Ollama format
        ollama_messages = []
        for msg in messages:
            message_dict = {
                "role": msg.role,
                "content": msg.content
            }
            if msg.tool_calls:
                message_dict["tool_calls"] = msg.tool_calls
            ollama_messages.append(message_dict)

        try:
            stream = await asyncio.to_thread(
                self.client.chat,
                model=model,
                messages=ollama_messages,
                tools=tools,
                stream=True
            )

            for chunk in stream:
                if 'message' in chunk and 'content' in chunk['message']:
                    yield chunk['message']['content']

        except Exception as e:
            logging.error(f"Ollama streaming error: {e}", exc_info=True)
            raise

    def list_models(self) -> List[str]:
        """List available Ollama models."""
        try:
            response = self.client.list()
            return [model['name'] for model in response.get('models', [])]
        except Exception as e:
            logging.error(f"Failed to list Ollama models: {e}")
            return []

    def get_provider_name(self) -> str:
        """Get provider name."""
        return "ollama"

    def _extract_tool_request(self, response: Dict) -> Dict:
        """
        Extract tool calls from response content if they're embedded in text.
        This handles cases where the model outputs <tool_call> tags instead of proper tool calls.
        """
        content = response.get('message', {}).get('content', '')

        tool_call_match = re.search(r'<tool_call>(.*?)</tool_call>', content, re.DOTALL)
        if tool_call_match:
            tool_call_content = tool_call_match.group(1)
            try:
                tool_call = json.loads(tool_call_content)
                logging.info("Tool call corrected from text format.")
                return {
                    "model": response.get("model", "unknown_model"),
                    "created_at": response.get('created_at', "unknown_created_at"),
                    "message": {
                        "role": response.get('message', {}).get("role", "assistant"),
                        "content": "",
                        "tool_calls": [{
                            "function": {
                                "name": tool_call.get("name", "unknown_name"),
                                "arguments": tool_call.get("args", tool_call.get("arguments", {}))
                            }
                        }]
                    }
                }
            except json.JSONDecodeError as e:
                logging.error(f"Failed to parse tool call JSON: {e}")
                return response

        return response
