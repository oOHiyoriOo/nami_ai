"""
Abstract base class for AI providers.
Allows easy integration of different AI backends (Ollama, OpenAI, Anthropic, etc.)
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, AsyncIterator
from dataclasses import dataclass


@dataclass
class Message:
    """Standardized message format across providers."""
    role: str  # system, user, assistant, tool
    content: str
    name: Optional[str] = None  # For tool messages
    tool_calls: Optional[List[Dict]] = None  # For assistant tool calls


@dataclass
class ChatResponse:
    """Standardized chat response format."""
    content: str
    tool_calls: Optional[List[Dict]] = None
    model: str = "unknown"
    finish_reason: str = "stop"
    usage: Optional[Dict[str, int]] = None


class AIProvider(ABC):
    """Abstract base class for AI providers."""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the provider with configuration.

        Args:
            config: Provider-specific configuration
        """
        self.config = config

    @abstractmethod
    async def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        **kwargs
    ) -> ChatResponse:
        """
        Generate a chat completion.

        Args:
            messages: List of messages in the conversation
            tools: Optional list of tools available to the model
            **kwargs: Additional provider-specific parameters

        Returns:
            ChatResponse with the model's response
        """
        pass

    @abstractmethod
    async def chat_stream(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """
        Generate a streaming chat completion.

        Args:
            messages: List of messages in the conversation
            tools: Optional list of tools available to the model
            **kwargs: Additional provider-specific parameters

        Yields:
            Chunks of the response as they are generated
        """
        pass

    @abstractmethod
    def list_models(self) -> List[str]:
        """
        List available models for this provider.

        Returns:
            List of model names/identifiers
        """
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        """
        Get the name of this provider.

        Returns:
            Provider name (e.g., "ollama", "openai", "anthropic")
        """
        pass

    def supports_tools(self) -> bool:
        """
        Check if this provider supports tool/function calling.

        Returns:
            True if tool calling is supported
        """
        return True

    def supports_streaming(self) -> bool:
        """
        Check if this provider supports streaming responses.

        Returns:
            True if streaming is supported
        """
        return True

    def normalize_message(self, msg: Dict[str, Any]) -> Message:
        """
        Convert a dictionary message to standardized Message format.

        Args:
            msg: Message dictionary

        Returns:
            Standardized Message object
        """
        return Message(
            role=msg.get("role", "user"),
            content=msg.get("content", ""),
            name=msg.get("name"),
            tool_calls=msg.get("tool_calls")
        )

    def normalize_messages(self, messages: List[Dict[str, Any]]) -> List[Message]:
        """
        Convert a list of dictionary messages to standardized Message format.

        Args:
            messages: List of message dictionaries

        Returns:
            List of standardized Message objects
        """
        return [self.normalize_message(msg) for msg in messages]
