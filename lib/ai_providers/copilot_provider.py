"""
GitHub Copilot AI provider implementation via copilot-api proxy.

This provider connects to the copilot-api proxy server which exposes
GitHub Copilot as an OpenAI-compatible API.

Repository: https://github.com/ericc-ch/copilot-api
"""
import logging
from typing import Any

import httpx

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

    def __init__(self, config: dict[str, Any]):
        """
        Initialize Copilot provider.

        Args:
            config: Configuration dict with:
                - url: copilot-api server URL (default: http://localhost:4141)
                - model: model name (default: gpt-4.1)
                - api_key: API key (default: dummy)
                - request_timeout: seconds for read/write before giving up (0 = no limit, default: 600)
                - connect_timeout: seconds to establish TCP connection (default: 30)
                - max_openai_retries: openai SDK internal retry count (default: 0; use retry_max_attempts in bot config instead)
        """
        super().__init__(config)
        self.capabilities = {"completion", "tools", "structured_output"}
        self.base_url = config.get('url', 'http://localhost:4141')
        self.default_model = config.get('model', 'gpt-4.1')
        self.api_key = config.get('api_key', 'dummy')

        request_timeout = config.get('request_timeout', 600)
        connect_timeout = config.get('connect_timeout', 30)
        max_retries = config.get('max_openai_retries', 0)
        timeout = httpx.Timeout(
            connect=connect_timeout,
            read=request_timeout if request_timeout > 0 else None,
            write=request_timeout if request_timeout > 0 else None,
            pool=request_timeout if request_timeout > 0 else None,
        )

        try:
            from openai import AsyncOpenAI
            self.client = AsyncOpenAI(
                base_url=f"{self.base_url}/v1",
                api_key=self.api_key,
                timeout=timeout,
                max_retries=max_retries,
            )
            logging.info(f"Initialized Copilot provider with model: {self.default_model}")
            logging.info(f"Connected to copilot-api at: {self.base_url} (connect={connect_timeout}s, read={request_timeout or '∞'}s, max_retries={max_retries})")
        except ImportError:
            raise ImportError(
                "openai package not installed. Install with: pip install openai"
            )

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        **kwargs
    ) -> ChatResponse:
        """Generate a chat completion using GitHub Copilot."""
        try:
            return await self._openai_compatible_chat(messages, tools, **kwargs)
        except Exception as e:
            logging.error(f"Copilot chat error: {e}", exc_info=True)
            raise

    def list_models(self) -> list[str]:
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

