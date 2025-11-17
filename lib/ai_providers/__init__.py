"""
AI Provider system for dynamic backend switching.
"""
import logging
from typing import Dict, Any, Type, Optional

from .base_provider import AIProvider, Message, ChatResponse
from .ollama_provider import OllamaProvider


class ProviderRegistry:
    """Registry for AI providers."""

    _providers: Dict[str, Type[AIProvider]] = {
        "ollama": OllamaProvider,
    }

    _instances: Dict[str, AIProvider] = {}

    @classmethod
    def register_provider(cls, name: str, provider_class: Type[AIProvider]):
        """
        Register a new AI provider.

        Args:
            name: Provider name (e.g., "openai", "anthropic")
            provider_class: Provider class inheriting from AIProvider
        """
        cls._providers[name] = provider_class
        logging.info(f"Registered AI provider: {name}")

    @classmethod
    def get_provider(cls, name: str, config: Dict[str, Any]) -> AIProvider:
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
        logging.debug("Anthropic provider not available")


# Auto-register optional providers on import
_register_optional_providers()


__all__ = [
    "AIProvider",
    "Message",
    "ChatResponse",
    "OllamaProvider",
    "ProviderRegistry"
]
