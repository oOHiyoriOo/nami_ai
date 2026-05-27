"""
Abstract base class for chat adapters.
Defines the interface that all chat clients (Discord, Telegram, CLI, etc.) must implement.
"""
from abc import ABC, abstractmethod
from typing import Callable, Awaitable, Any
from contextlib import asynccontextmanager

from lib.chat_adapters.types import (
    ChatMessage,
    ChatUser,
    ChatChannel,
    ChatResponse,
)


class BaseChatAdapter(ABC):
    """
    Abstract base class for chat client adapters.
    
    This class defines the contract that all chat clients must follow to integrate
    with the AI workflow. Implementations handle platform-specific details while
    exposing a unified interface.
    
    Usage:
        class DiscordAdapter(BaseChatAdapter):
            async def send_message(self, channel, content, **kwargs):
                # Discord-specific implementation
                pass
    """
    
    def __init__(self, config: dict):
        """
        Initialize the chat adapter.
        
        Args:
            config: Configuration dictionary for the adapter
        """
        self.config = config
        self._message_handlers: list[Callable[[ChatMessage], Awaitable[None]]] = []
        self._connected = False
    
    # ==================== Connection Management ====================
    
    @abstractmethod
    async def connect(self) -> None:
        """
        Establish connection to the chat platform.
        This should be called before any other operations.
        """
        ...
    
    @abstractmethod
    async def disconnect(self) -> None:
        """
        Gracefully disconnect from the chat platform.
        """
        ...
    
    @property
    def is_connected(self) -> bool:
        """Check if the adapter is connected to the platform."""
        return self._connected
    
    # ==================== Bot Identity ====================
    
    @abstractmethod
    def get_bot_user(self) -> ChatUser:
        """
        Get the bot's user information.
        
        Returns:
            ChatUser representing the bot
        """
        ...
    
    @abstractmethod
    def get_bot_id(self) -> str:
        """
        Get the bot's user ID.
        
        Returns:
            Bot's unique identifier as string
        """
        ...
    
    # ==================== Message Sending ====================
    
    @abstractmethod
    async def send_message(
        self,
        channel: ChatChannel,
        content: str,
        reply_to: ChatMessage | None = None,
        **kwargs
    ) -> ChatMessage:
        """
        Send a message to a channel.
        
        Args:
            channel: Target channel
            content: Message content
            reply_to: Optional message to reply to
            **kwargs: Platform-specific options
            
        Returns:
            The sent message as ChatMessage
        """
        ...
    
    @abstractmethod
    async def send_response(self, response: ChatResponse) -> ChatMessage:
        """
        Send a ChatResponse object.
        
        Args:
            response: The response to send
            
        Returns:
            The sent message as ChatMessage
        """
        ...
    
    @abstractmethod
    async def edit_message(
        self,
        message: ChatMessage,
        new_content: str
    ) -> ChatMessage:
        """
        Edit an existing message.
        
        Args:
            message: The message to edit
            new_content: New content for the message
            
        Returns:
            The edited message
        """
        ...
    
    # ==================== Message Handling ====================
    
    def register_message_handler(
        self,
        handler: Callable[[ChatMessage], Awaitable[None]]
    ) -> None:
        """
        Register a handler for incoming messages.
        
        Args:
            handler: Async function that receives ChatMessage
        """
        self._message_handlers.append(handler)
    
    def unregister_message_handler(
        self,
        handler: Callable[[ChatMessage], Awaitable[None]]
    ) -> None:
        """
        Remove a registered message handler.
        
        Args:
            handler: The handler to remove
        """
        if handler in self._message_handlers:
            self._message_handlers.remove(handler)
    
    async def _dispatch_message(self, message: ChatMessage) -> None:
        """
        Dispatch a message to all registered handlers.
        
        Args:
            message: The message to dispatch
        """
        for handler in self._message_handlers:
            try:
                await handler(message)
            except Exception as e:
                # Log but don't propagate to allow other handlers to run
                import logging
                logging.error(f"Error in message handler: {e}", exc_info=True)
    
    # ==================== History & Context ====================
    
    @abstractmethod
    async def get_message_by_id(
        self,
        channel: ChatChannel,
        message_id: str
    ) -> ChatMessage | None:
        """
        Retrieve a specific message by ID.
        
        Args:
            channel: The channel containing the message
            message_id: The message ID to retrieve
            
        Returns:
            ChatMessage if found, None otherwise
        """
        ...
    
    # ==================== Typing Indicator ====================
    
    @abstractmethod
    @asynccontextmanager
    async def typing(self, channel: ChatChannel):
        """
        Context manager for showing typing indicator.
        
        Usage:
            async with adapter.typing(channel):
                # Do work while typing indicator is shown
                response = await generate_response()
        """
        ...
    
    # ==================== Thread Support ====================
    
    @abstractmethod
    async def create_thread(
        self,
        message: ChatMessage,
        name: str
    ) -> ChatChannel:
        """
        Create a thread from a message.
        
        Args:
            message: The message to create a thread from
            name: Name for the thread
            
        Returns:
            ChatChannel representing the thread
        """
        ...
    
    @abstractmethod
    async def send_to_thread(
        self,
        thread: ChatChannel,
        content: str
    ) -> ChatMessage:
        """
        Send a message to a thread.
        
        Args:
            thread: The thread channel
            content: Message content
            
        Returns:
            The sent message
        """
        ...
    
    # ==================== Status Updates ====================
    
    @abstractmethod
    async def set_status(self, status: str) -> None:
        """
        Set the bot's status/presence.
        
        Args:
            status: Status text to display
        """
        ...
    
    # ==================== Utility Methods ====================
    
    @abstractmethod
    def should_respond(self, message: ChatMessage) -> bool:
        """
        Determine if the bot should respond to a message.
        
        This method implements platform-specific logic for determining
        when the bot should generate a response (e.g., when mentioned,
        in specific channels, etc.)
        
        Args:
            message: The incoming message
            
        Returns:
            True if the bot should respond, False otherwise
        """
        ...
    
    @abstractmethod
    async def convert_to_chat_message(self, raw_message: Any) -> ChatMessage:
        """
        Convert a platform-specific message to ChatMessage.
        
        Args:
            raw_message: The platform's native message object
            
        Returns:
            Standardized ChatMessage
        """
        ...
    
    def get_adapter_name(self) -> str:
        """
        Get the name of this adapter.
        
        Returns:
            Adapter name (e.g., "discord", "telegram", "cli")
        """
        return self.__class__.__name__.replace("Adapter", "").lower()
    
    # ==================== Permission Checking ====================
    
    @abstractmethod
    def is_permitted_user(self, user: ChatUser) -> bool:
        """
        Check if a user has permission to use certain features.
        
        Args:
            user: The user to check
            
        Returns:
            True if the user is permitted
        """
        ...
    
    @abstractmethod
    def is_ai_channel(self, channel: ChatChannel) -> bool:
        """
        Check if a channel is configured for AI responses.
        
        Args:
            channel: The channel to check
            
        Returns:
            True if the channel is an AI channel
        """
        ...
