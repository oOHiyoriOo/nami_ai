"""
OpenAI AI provider implementation.
"""
import logging
from typing import Any

import httpx

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

    def __init__(self, config: dict[str, Any]):
        """
        Initialize OpenAI provider.

        Args:
            config: Configuration dict with 'api_key' and 'model' keys.
                - request_timeout: seconds for read/write before giving up (0 = no limit, default: 600)
                - connect_timeout: seconds to establish TCP connection (default: 30)
        """
        super().__init__(config)
        self.capabilities = {"completion", "tools", "vision", "structured_output"}
        self.api_key = config.get('api_key')
        self.default_model = config.get('model', 'gpt-4')
        self.organization = config.get('organization')

        if not self.api_key:
            raise ValueError("OpenAI API key is required")

        request_timeout = config.get('request_timeout', 600)
        connect_timeout = config.get('connect_timeout', 30)
        timeout = httpx.Timeout(
            connect=connect_timeout,
            read=request_timeout if request_timeout > 0 else None,
            write=request_timeout if request_timeout > 0 else None,
            pool=request_timeout if request_timeout > 0 else None,
        )

        try:
            from openai import AsyncOpenAI
            self.client = AsyncOpenAI(
                api_key=self.api_key,
                organization=self.organization,
                timeout=timeout,
            )
            logging.info(f"Initialized OpenAI provider with model: {self.default_model} (connect={connect_timeout}s, read={request_timeout or '∞'}s)")
        except ImportError:
            raise ImportError("openai package not installed. Install with: pip install openai")

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        **kwargs
    ) -> ChatResponse:
        """Generate a chat completion using OpenAI."""
        try:
            return await self._openai_compatible_chat(messages, tools, **kwargs)
        except Exception as e:
            logging.error(f"OpenAI chat error: {e}", exc_info=True)
            raise

    def list_models(self) -> list[str]:
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
