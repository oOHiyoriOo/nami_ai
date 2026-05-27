"""
Standardized types for chat adapters.
These types provide a client-agnostic representation of chat entities.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from enum import Enum

class MessageType(Enum):
    """Type of message."""
    DEFAULT = "default"
    REPLY = "reply"
    SYSTEM = "system"
    COMMAND = "command"


@dataclass
class ChatAttachment:
    """Represents a file attachment in a message."""
    filename: str
    url: str
    content_type: str | None = None
    size: int | None = None
    
    @property
    def is_image(self) -> bool:
        return self.content_type.startswith("image/") if self.content_type else False
    
    @property
    def is_video(self) -> bool:
        return self.content_type.startswith("video/") if self.content_type else False
    
    @property
    def is_audio(self) -> bool:
        return self.content_type.startswith("audio/") if self.content_type else False


@dataclass
class ChatUser:
    """Represents a user in a chat platform."""
    id: str
    name: str
    display_name: str | None = None
    avatar_url: str | None = None
    is_bot: bool = False
    created_at: datetime | None = None
    roles: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if self.display_name is None:
            self.display_name = self.name


@dataclass
class ChatChannel:
    """Represents a channel/conversation in a chat platform."""
    id: str
    name: str
    is_dm: bool = False
    guild_id: str | None = None
    guild_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatMessage:
    """
    Platform-agnostic representation of a chat message.
    This is the standardized format that the AI workflow receives.
    """
    id: str
    content: str
    author: ChatUser
    channel: ChatChannel
    timestamp: datetime
    message_type: MessageType = MessageType.DEFAULT
    reply_to_id: str | None = None
    reply_to_content: str | None = None
    reply_context: str | None = None
    attachments: list[ChatAttachment] = field(default_factory=list)
    mentions_bot: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    
    # Reference to the original platform-specific message object
    # This allows adapters to access platform-specific features if needed
    _raw_message: Any = field(default=None, repr=False)
    
    @property
    def conversation_id(self) -> str:
        """Get a unique conversation identifier."""
        return self.channel.id


@dataclass
class ChatResponse:
    """
    Represents a response to be sent through the chat adapter.
    """
    content: str
    reply_to: ChatMessage | None = None
    attachments: list[str] = field(default_factory=list)  # File paths to attach
    create_thread: bool = False
    thread_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TypingIndicator:
    """Context manager data for typing indicators."""
    channel: ChatChannel
    active: bool = True
