"""
OpenAI AI provider implementation.
"""
import logging
from typing import List, Dict, Any, Optional, AsyncIterator

from .base_provider import AIProvider, Message, ChatResponse


class OpenAIProvider(AIProvider):
    """
    OpenAI AI provider.

    To use this provider:
    1. Install: pip install openai
    2. Configure in config.yml:
       providers:
         openai:
           api_key: "your-api-key"
           model: "gpt-4"
           organization: "your-org-id"  # optional
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize OpenAI provider.

        Args:
            config: Configuration dict with 'api_key' and 'model' keys
        """
        super().__init__(config)
        self.api_key = config.get('api_key')
        self.default_model = config.get('model', 'gpt-4')
        self.organization = config.get('organization')

        if not self.api_key:
            raise ValueError("OpenAI API key is required")

        try:
            from openai import AsyncOpenAI
            self.client = AsyncOpenAI(
                api_key=self.api_key,
                organization=self.organization
            )
            logging.info(f"Initialized OpenAI provider with model: {self.default_model}")
        except ImportError:
            raise ImportError("openai package not installed. Install with: pip install openai")

    async def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        **kwargs
    ) -> ChatResponse:
        """Generate a chat completion using OpenAI."""
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
                }
            )

        except Exception as e:
            logging.error(f"OpenAI chat error: {e}", exc_info=True)
            raise

    async def chat_stream(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """Generate a streaming chat completion using OpenAI."""
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
            logging.error(f"OpenAI streaming error: {e}", exc_info=True)
            raise

    def list_models(self) -> List[str]:
        """List available OpenAI models."""
        # Return commonly used models
        return [
            "gpt-4",
            "gpt-4-turbo",
            "gpt-4o",
            "gpt-3.5-turbo",
            "gpt-3.5-turbo-16k"
        ]

    def get_provider_name(self) -> str:
        """Get provider name."""
        return "openai"
