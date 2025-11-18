"""
Context builder service - constructs conversation context.
Single responsibility: Building message context with personality and memories.
"""
import logging
from typing import List, Optional
from lib.services.memory_service import MemoryService


class MessageContext:
    """Container for message context components."""

    def __init__(self):
        self.system_messages = []
        self.original_messages = []

    def add_system_message(self, content: str):
        """Add a system message to context."""
        self.system_messages.append({"role": "system", "content": content})

    def add_original_messages(self, messages: List[dict]):
        """Add original messages."""
        self.original_messages = messages

    def build(self) -> List[dict]:
        """Build final message list."""
        return self.system_messages + self.original_messages


class ContextBuilder:
    """Builds conversation context with personality and memories."""

    def __init__(self, system_prompt_provider, memory_service: Optional[MemoryService] = None):
        """
        Initialize context builder.

        Args:
            system_prompt_provider: Provider for system prompts
            memory_service: Optional memory service
        """
        self.system_prompt_provider = system_prompt_provider
        self.memory_service = memory_service

    async def build_context(
        self,
        messages: List[dict],
        user_id: Optional[str] = None,
        enable_personality: bool = True,
        enable_memory: bool = True
    ) -> List[dict]:
        """
        Build conversation context.

        Args:
            messages: Original messages
            user_id: User identifier
            enable_personality: Include personality prompt
            enable_memory: Include memories

        Returns:
            Enhanced message list
        """
        context = MessageContext()

        # Add personality prompt
        if enable_personality:
            await self._add_personality(context)

        # Add user context
        if user_id:
            self._add_user_context(context, user_id)

        # Add memories
        if enable_memory and user_id and self.memory_service:
            await self._add_memories(context, messages, user_id)

        # Add original messages
        context.add_original_messages(messages)

        return context.build()

    async def _add_personality(self, context: MessageContext):
        """Add personality prompt to context."""
        try:
            prompt = await self.system_prompt_provider.get_prompt()
            context.add_system_message(prompt)
        except Exception as e:
            logging.error(f"Error loading personality prompt: {e}")

    def _add_user_context(self, context: MessageContext, user_id: str):
        """Add user context to messages."""
        user_context = f"Context: You are talking to user ID '{user_id}'"
        context.add_system_message(user_context)

    async def _add_memories(self, context: MessageContext, messages: List[dict], user_id: str):
        """Add relevant memories to context."""
        try:
            # Get last user message for memory search
            user_messages = [msg for msg in messages if msg.get("role") == "user"]
            if not user_messages:
                return

            last_user_msg = user_messages[-1].get("content", "")
            if not last_user_msg:
                return

            # Retrieve and format memories
            formatted_memories = await self.memory_service.get_formatted_memories(
                query=last_user_msg,
                user_id=user_id,
                top_k=5,
                context_k=20
            )

            if formatted_memories:
                context.add_system_message(formatted_memories)

        except Exception as e:
            logging.error(f"Error adding memories: {e}", exc_info=True)
