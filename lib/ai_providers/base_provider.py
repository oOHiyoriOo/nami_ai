"""
Abstract base class for AI providers.
Allows easy integration of different AI backends (Ollama, OpenAI, Anthropic, etc.)
"""
from abc import ABC, abstractmethod
from typing import Any
from dataclasses import dataclass

from lib.utils.tool_parser import extract_tool_from_xml


@dataclass
class Message:
    """Standardized message format across providers."""
    role: str  # system, user, assistant, tool
    content: str | None = None
    name: str | None = None  # For tool messages
    tool_calls: list[dict] | None = None  # For assistant tool calls
    images: list[str] | None = None  # Base64-encoded images for vision
    tool_call_id: str | None = None  # Required by OpenAI/Copilot for tool result messages


@dataclass
class ChatResponse:
    """Standardized chat response format."""
    content: str
    tool_calls: list[dict] | None = None
    model: str = "unknown"
    finish_reason: str = "stop"
    usage: dict[str, int] | None = None
    thinking: str | None = None  # Thinking/reasoning content (separate from main response)


class AIProvider(ABC):
    """Abstract base class for AI providers."""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize the provider with configuration.

        Args:
            config: Provider-specific configuration
        """
        self.config = config
        self.capabilities: set[str] = set()

    def _normalize_messages(
        self,
        messages: list[Message],
        include_name: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Convert Message objects to provider-specific dict format.

        Args:
            messages: List of Message objects
            include_name: Include 'name' field (not supported by all providers, e.g. Ollama)

        Returns:
            List of message dictionaries
        """
        normalized = []
        for msg in messages:
            message_dict: dict[str, Any] = {
                "role": msg.role,
                "content": msg.content
            }
            if include_name and msg.name:
                message_dict["name"] = msg.name
            if msg.tool_calls:
                message_dict["tool_calls"] = msg.tool_calls
            if msg.tool_call_id:
                message_dict["tool_call_id"] = msg.tool_call_id
            if msg.images:
                message_dict["images"] = msg.images
            normalized.append(message_dict)
        return normalized

    def _extract_tool_from_xml(self, response: dict) -> dict:
        """
        Extract tool calls from response content if they're embedded in XML tags.
        Delegates to the shared utility function.
        """
        return extract_tool_from_xml(response)

    async def _openai_compatible_chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        **kwargs
    ) -> ChatResponse:
        """Shared chat implementation for OpenAI-compatible providers.

        Both OpenAIProvider and CopilotProvider use the openai library's
        AsyncOpenAI client and share identical request/response handling.
        This method centralizes that logic so each provider's chat() is a
        thin delegating wrapper.
        """
        model = kwargs.get('model', self.default_model)
        openai_messages = self._normalize_messages(messages)

        request_params: dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
        }

        if tools:
            request_params["tools"] = [
                {
                    "type": "function",
                    "function": tool.get("function", tool)
                }
                for tool in tools
            ]

        response = await self.client.chat.completions.create(**request_params)
        message = response.choices[0].message

        tool_calls = None
        if message.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
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

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
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
    def list_models(self) -> list[str]:
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
        """Check if the current model supports tool/function calling."""
        return "tools" in self.capabilities

    def supports_vision(self) -> bool:
        """Check if the current model supports vision/image input."""
        return "vision" in self.capabilities

    def supports_thinking(self) -> bool:
        """Check if the current model supports thinking/reasoning."""
        return "thinking" in self.capabilities

    def supports_structured_output(self) -> bool:
        """
        Check if the provider supports structured output (JSON schema enforcement).
        
        Structured output allows passing a JSON schema that the model must follow,
        guaranteeing valid JSON responses without manual parsing/retry logic.
        
        Returns:
            True if provider supports structured output, False otherwise
        """
        return "structured_output" in self.capabilities

    def ensure_capabilities(self, model: str) -> None:
        """
        Ensure capabilities are known for the given model.
        
        Subclasses override this to lazily query and cache capabilities
        when a model name is first available. The base implementation
        is a no-op — capabilities are assumed to be set at init time.
        
        Args:
            model: Model name to ensure capabilities for
        """
        pass

    def query_model_capabilities(self, model: str | None = None) -> set[str]:
        """
        Query and cache the capabilities of a model.
        Override in subclasses to implement provider-specific logic.

        Returns:
            Set of capability strings (e.g. {"completion", "tools", "vision"})
        """
        return self.capabilities

    def normalize_message(self, msg: dict[str, Any]) -> Message:
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

    def normalize_messages(self, messages: list[dict[str, Any]]) -> list[Message]:
        """
        Convert a list of dictionary messages to standardized Message format.

        Args:
            messages: List of message dictionaries

        Returns:
            List of standardized Message objects
        """
        return [self.normalize_message(msg) for msg in messages]
