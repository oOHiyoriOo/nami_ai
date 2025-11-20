"""
GitHub Copilot AI provider implementation via copilot-api proxy.

This provider connects to the copilot-api proxy server which exposes
GitHub Copilot as an OpenAI-compatible API.

Repository: https://github.com/ericc-ch/copilot-api
"""
import logging
from typing import List, Dict, Any, Optional, AsyncIterator

from .base_provider import AIProvider, Message, ChatResponse


class CopilotProvider(AIProvider):
    """
    GitHub Copilot AI provider via copilot-api proxy.

    Prerequisites:
    1. Install copilot-api (see external/copilot-api/README.md)
    2. Start copilot-api server: npx copilot-api@latest start
    3. Configure in config.yml:
       providers:
         copilot:
           url: "http://localhost:4141"  # copilot-api server URL
           model: "gpt-4.1"               # or other available models
           api_key: "dummy"               # copilot-api uses dummy auth

    Available models (from copilot-api):
    - gpt-4.1: Latest GPT-4 model via Copilot
    - gpt-4o: GPT-4 optimized
    - gpt-4-turbo: GPT-4 Turbo
    - gpt-3.5-turbo: GPT-3.5 Turbo
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Copilot provider.

        Args:
            config: Configuration dict with:
                - url: copilot-api server URL (default: http://localhost:4141)
                - model: model name (default: gpt-4.1)
                - api_key: API key (default: dummy)
        """
        super().__init__(config)
        self.base_url = config.get('url', 'http://localhost:4141')
        self.default_model = config.get('model', 'gpt-4.1')
        self.api_key = config.get('api_key', 'dummy')

        try:
            from openai import AsyncOpenAI
            self.client = AsyncOpenAI(
                base_url=f"{self.base_url}/v1",
                api_key=self.api_key
            )
            logging.info(f"Initialized Copilot provider with model: {self.default_model}")
            logging.info(f"Connected to copilot-api at: {self.base_url}")
        except ImportError:
            raise ImportError(
                "openai package not installed. Install with: pip install openai"
            )

    async def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        **kwargs
    ) -> ChatResponse:
        """Generate a chat completion using GitHub Copilot."""
        model = kwargs.get('model', self.default_model)

        # Convert Message objects to OpenAI format
        openai_messages = []
        for msg in messages:
            message_dict = {
                "role": msg.role,
                "content": msg.content
            }
            if msg.name:
                message_dict["name"] = msg.name
            if msg.tool_calls:
                message_dict["tool_calls"] = msg.tool_calls
            openai_messages.append(message_dict)

        try:
            # Build request parameters
            request_params = {
                "model": model,
                "messages": openai_messages,
            }

            if tools:
                # Convert to OpenAI tools format
                openai_tools = [
                    {
                        "type": "function",
                        "function": tool.get("function", tool)
                    }
                    for tool in tools
                ]
                request_params["tools"] = openai_tools

            response = await self.client.chat.completions.create(**request_params)

            message = response.choices[0].message

            # Extract tool calls if present
            tool_calls = None
            if message.tool_calls:
                tool_calls = [
                    {
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    }
                    for tc in message.tool_calls
                ]

            return ChatResponse(
                content=message.content or "",
                tool_calls=tool_calls,
                model=response.model,
                finish_reason=response.choices[0].finish_reason,
                usage={
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens
                } if response.usage else None
            )

        except Exception as e:
            logging.error(f"Copilot chat error: {e}", exc_info=True)
            raise

    async def chat_stream(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """Generate a streaming chat completion using GitHub Copilot."""
        model = kwargs.get('model', self.default_model)

        # Convert Message objects to OpenAI format
        openai_messages = []
        for msg in messages:
            message_dict = {
                "role": msg.role,
                "content": msg.content
            }
            if msg.name:
                message_dict["name"] = msg.name
            openai_messages.append(message_dict)

        try:
            request_params = {
                "model": model,
                "messages": openai_messages,
                "stream": True
            }

            if tools:
                openai_tools = [
                    {
                        "type": "function",
                        "function": tool.get("function", tool)
                    }
                    for tool in tools
                ]
                request_params["tools"] = openai_tools

            stream = await self.client.chat.completions.create(**request_params)

            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except Exception as e:
            logging.error(f"Copilot streaming error: {e}", exc_info=True)
            raise

    def list_models(self) -> List[str]:
        """List available Copilot models."""
        return [
            "gpt-4.1",         # Latest GPT-4 via Copilot
            "gpt-4o",          # GPT-4 optimized
            "gpt-4-turbo",     # GPT-4 Turbo
            "gpt-3.5-turbo",   # GPT-3.5 Turbo
        ]

    def get_provider_name(self) -> str:
        """Get provider name."""
        return "copilot"

    def supports_tools(self) -> bool:
        """Check if provider supports tool/function calling."""
        return True

    def supports_streaming(self) -> bool:
        """Check if provider supports streaming responses."""
        return True
