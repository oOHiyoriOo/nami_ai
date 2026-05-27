"""
Chat Adapters Package

Provides abstraction layer for different chat clients (Discord, Telegram, CLI, etc.)
"""
from lib.chat_adapters.base_adapter import BaseChatAdapter
from lib.chat_adapters.types import ChatMessage, ChatUser, ChatChannel, ChatAttachment, ChatResponse, MessageType

__all__ = [
    'BaseChatAdapter',
    'ChatMessage',
    'ChatUser',
    'ChatChannel',
    'ChatAttachment',
    'ChatResponse',
    'MessageType',
]
