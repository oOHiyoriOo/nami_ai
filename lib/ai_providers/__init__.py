"""
AI Provider system for dynamic backend switching.
"""
import logging
from typing import Any

from .base_provider import AIProvider, Message, ChatResponse
from .ollama_provider import OllamaProvider


class ProviderRegistry:
    """Registry for AI providers."""

    _providers: dict[str, type[AIProvider]] = {
        "ollama": OllamaProvider,
    }

    _instances: dict[str, AIProvider] = {}

    @classmethod
    def register_provider(cls, name: str, provider_class: type[AIProvider]):
        """
        Register a new AI provider.

        Args:
            name: Provider name (e.g., "openai", "anthropic")
            provider_class: Provider class inheriting from AIProvider
        """
        cls._providers[name] = provider_class
        logging.info(f"Registered AI provider: {name}")

    @classmethod
    def get_provider(cls, name: str, config: dict[str, Any]) -> AIProvider:
        """
        Get or create a provider instance.

        Args:
            name: Provider name
            config: Provider configuration

        Returns:
            AIProvider instance

        Raises:
            ValueError: If provider is not registered
        """
        # Return cached instance if exists
        if name in cls._instances:
            return cls._instances[name]

        # Create new instance
        if name not in cls._providers:
            raise ValueError(
                f"Provider '{name}' not registered. "
                f"Available providers: {list(cls._providers.keys())}"
            )

        provider_class = cls._providers[name]
        provider_instance = provider_class(config)
        cls._instances[name] = provider_instance

        logging.info(f"Created AI provider instance: {name}")
        return provider_instance

    @classmethod
    def list_providers(cls) -> list:
        """
        List all registered providers.

        Returns:
            List of provider names
        """
        return list(cls._providers.keys())

    @classmethod
    def get_or_create(
        cls, provider_name: str, providers_config: dict
    ) -> tuple['AIProvider | None', 'str | None']:
        """Get a cached provider or create and cache a new instance.

        The cache is stored in the global registry (``g_data``) so it is
        shared between the API server and background pipeline handler.

        Args:
            provider_name: Name of the provider (e.g. ``"ollama"``).
            providers_config: Provider configuration dict keyed by name.

        Returns:
            ``(provider, None)`` on success, ``(None, error_message)`` on
            failure.
        """
        from lib.global_registry import g_data

        cache_key = f"provider_{provider_name}"
        cached = g_data.get(cache_key)
        if cached:
            return cached, None

        provider_config = providers_config.get(provider_name)
        if not provider_config:
            return (
                None,
                f"Provider '{provider_name}' not configured. "
                f"Available: {list(providers_config.keys())}",
            )

        try:
            provider = cls.get_provider(provider_name, provider_config)
            g_data.get_or_create(cache_key, lambda: provider)
            logging.info(f"Initialized provider: {provider_name}")
            return provider, None
        except Exception as e:
            return None, str(e)

    @classmethod
    def clear_instances(cls):
        """Clear all cached provider instances."""
        cls._instances.clear()


# Attempt to import optional providers
def _register_optional_providers():
    """Register optional providers if their dependencies are available."""

    # Try to import OpenAI provider
    try:
        from .openai_provider import OpenAIProvider
        ProviderRegistry.register_provider("openai", OpenAIProvider)
    except ImportError:
        logging.debug("OpenAI provider not available (missing openai package)")

    # Try to import Anthropic provider (template for future)
    try:
        from .anthropic_provider import AnthropicProvider
        ProviderRegistry.register_provider("anthropic", AnthropicProvider)
    except ImportError:
        pass  # Not implemented yet

    # Try to import Copilot provider
    try:
        from .copilot_provider import CopilotProvider
        ProviderRegistry.register_provider("copilot", CopilotProvider)
    except ImportError:
        logging.debug("Copilot provider not available (missing openai package)")


# Auto-register optional providers on import
_register_optional_providers()


__all__ = [
    "AIProvider",
    "Message",
    "ChatResponse",
    "OllamaProvider",
    "OpenAIProvider",
    "CopilotProvider",
    "ProviderRegistry"
]
