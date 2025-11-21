"""
Discord Adapter - Translates Discord messages into AI-friendly format
Handles: reply chains, embeds, attachments, user context, reactions
"""
import discord
from typing import List, Dict, Any, Optional
from datetime import datetime


class DiscordMessageAdapter:
    """Adapts Discord messages to AI-understandable format."""

    def __init__(self, client: discord.Client):
        self.client = client

    async def format_message_for_ai(
        self,
        message: discord.Message,
        include_context: bool = True
    ) -> Dict[str, Any]:
        """
        Convert a Discord message to AI-friendly format.

        Args:
            message: Discord message object
            include_context: Whether to include reply chain context

        Returns:
            Dict with formatted message data
        """
        formatted = {
            "role": "assistant" if message.author.bot else "user",
            "content": await self._build_message_content(message),
            "metadata": self._extract_message_metadata(message)
        }

        # Include reply chain if this is a reply
        if include_context and message.reference:
            formatted["reply_to"] = await self._get_reply_chain(message)

        return formatted

    async def _build_message_content(self, message: discord.Message) -> str:
        """Build the full message content including all Discord elements."""
        parts = []

        # User identification
        user_context = self._format_user_context(message.author, message.guild)
        parts.append(user_context)

        # Main message content
        if message.content:
            parts.append(f"says: {message.content}")

        # Attachments
        if message.attachments:
            attachment_info = self._format_attachments(message.attachments)
            parts.append(attachment_info)

        # Embeds
        if message.embeds:
            embed_info = self._format_embeds(message.embeds)
            parts.append(embed_info)

        # Reactions (if significant)
        if message.reactions:
            reaction_info = self._format_reactions(message.reactions)
            if reaction_info:
                parts.append(reaction_info)

        # Stickers
        if message.stickers:
            sticker_info = self._format_stickers(message.stickers)
            parts.append(sticker_info)

        return "\n".join(parts)

    def _format_user_context(
        self,
        user: discord.User,
        guild: Optional[discord.Guild]
    ) -> str:
        """Format user context for AI understanding."""
        context_parts = [f"**{user.display_name}**"]

        # Add role info if in guild
        if guild and isinstance(user, discord.Member):
            roles = [role.name for role in user.roles if role.name != "@everyone"]
            if roles:
                context_parts.append(f"(roles: {', '.join(roles[:3])})")  # Top 3 roles

        # Add user ID for API tracking
        context_parts.append(f"[ID: {user.id}]")

        return " ".join(context_parts)

    def _format_attachments(self, attachments: List[discord.Attachment]) -> str:
        """Format attachment information."""
        if not attachments:
            return ""

        parts = ["attached:"]
        for att in attachments:
            if att.content_type:
                if att.content_type.startswith("image/"):
                    parts.append(f"  - 🖼️ Image: {att.filename} ({att.url})")
                elif att.content_type.startswith("video/"):
                    parts.append(f"  - 🎥 Video: {att.filename}")
                elif att.content_type.startswith("audio/"):
                    parts.append(f"  - 🎵 Audio: {att.filename}")
                else:
                    parts.append(f"  - 📎 File: {att.filename} ({att.size // 1024}KB)")
            else:
                parts.append(f"  - 📎 File: {att.filename}")

        return "\n".join(parts)

    def _format_embeds(self, embeds: List[discord.Embed]) -> str:
        """Format embed information."""
        if not embeds:
            return ""

        parts = ["shared embed:"]
        for embed in embeds:
            if embed.title:
                parts.append(f"  Title: {embed.title}")
            if embed.description:
                # Truncate long descriptions
                desc = embed.description[:200] + "..." if len(embed.description) > 200 else embed.description
                parts.append(f"  Description: {desc}")
            if embed.url:
                parts.append(f"  URL: {embed.url}")
            if embed.fields:
                parts.append(f"  Fields: {len(embed.fields)} custom fields")

        return "\n".join(parts)

    def _format_reactions(self, reactions: List[discord.Reaction]) -> Optional[str]:
        """Format reactions if they're significant."""
        # Only include reactions with 3+ counts (indicating community response)
        significant = [r for r in reactions if r.count >= 3]
        if not significant:
            return None

        parts = ["reactions:"]
        for reaction in significant:
            parts.append(f"  {reaction.emoji} x{reaction.count}")

        return "\n".join(parts)

    def _format_stickers(self, stickers: List[discord.StickerItem]) -> str:
        """Format sticker information."""
        if not stickers:
            return ""

        sticker_names = [f":{s.name}:" for s in stickers]
        return f"stickers: {', '.join(sticker_names)}"

    async def _get_reply_chain(self, message: discord.Message) -> List[Dict[str, Any]]:
        """Get the reply chain for context."""
        chain = []
        current = message

        # Traverse up the reply chain (max 5 levels to avoid infinite loops)
        for _ in range(5):
            if not current.reference:
                break

            try:
                # Fetch the referenced message
                ref_msg = await current.channel.fetch_message(current.reference.message_id)

                # Format the referenced message
                chain.insert(0, {
                    "user": ref_msg.author.display_name,
                    "user_id": str(ref_msg.author.id),
                    "content": ref_msg.content[:200],  # Truncate long messages
                    "timestamp": ref_msg.created_at.isoformat()
                })

                current = ref_msg
            except:
                break

        return chain

    def _extract_message_metadata(self, message: discord.Message) -> Dict[str, Any]:
        """Extract metadata for API tracking and context."""
        metadata = {
            "message_id": str(message.id),
            "user_id": str(message.author.id),
            "username": message.author.name,
            "display_name": message.author.display_name,
            "channel_id": str(message.channel.id),
            "timestamp": message.created_at.isoformat(),
            "is_bot": message.author.bot,
        }

        # Guild context
        if message.guild:
            metadata["guild_id"] = str(message.guild.id)
            metadata["guild_name"] = message.guild.name

            # Member-specific data
            if isinstance(message.author, discord.Member):
                metadata["joined_at"] = message.author.joined_at.isoformat() if message.author.joined_at else None
                metadata["roles"] = [role.name for role in message.author.roles if role.name != "@everyone"]

        # Thread context
        if isinstance(message.channel, discord.Thread):
            metadata["thread_name"] = message.channel.name
            metadata["parent_channel_id"] = str(message.channel.parent_id)

        return metadata

    async def get_conversation_context(
        self,
        channel: discord.TextChannel,
        current_message: discord.Message,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get recent conversation context with proper formatting.

        Args:
            channel: Discord channel
            current_message: The triggering message
            limit: Number of previous messages to include

        Returns:
            List of formatted messages for AI
        """
        messages = []

        # Get recent history
        async for msg in channel.history(limit=limit, before=current_message):
            # Skip system messages
            if msg.type != discord.MessageType.default:
                continue

            formatted = await self.format_message_for_ai(msg, include_context=True)
            messages.insert(0, formatted)

        # Add current message
        current_formatted = await self.format_message_for_ai(current_message, include_context=True)
        messages.append(current_formatted)

        return messages

    def format_for_api(
        self,
        messages: List[Dict[str, Any]],
        system_context: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """
        Format messages for Personality Proxy API.

        Args:
            messages: List of formatted Discord messages
            system_context: Optional system context to prepend

        Returns:
            API-compatible message format
        """
        api_messages = []

        # Add system context if provided
        if system_context:
            api_messages.append({
                "role": "system",
                "content": system_context
            })

        # Convert messages to API format
        for msg in messages:
            content = msg["content"]

            # Add reply context if present
            if "reply_to" in msg and msg["reply_to"]:
                reply_context = self._format_reply_context(msg["reply_to"])
                content = f"{reply_context}\n\n{content}"

            api_messages.append({
                "role": msg["role"],
                "content": content
            })

        return api_messages

    def _format_reply_context(self, reply_chain: List[Dict[str, Any]]) -> str:
        """Format reply chain for AI understanding."""
        if not reply_chain:
            return ""

        parts = ["[Replying to previous message(s):"]
        for msg in reply_chain:
            parts.append(f"  {msg['user']}: {msg['content'][:100]}")  # Truncate
        parts.append("]")

        return "\n".join(parts)

    def extract_user_id_for_api(self, message: discord.Message) -> str:
        """Extract user ID for API tracking."""
        return f"discord_{message.author.id}"

    def extract_conversation_id_for_api(self, message: discord.Message) -> str:
        """Extract conversation ID for API tracking."""
        # Use thread ID if in thread, otherwise channel ID
        if isinstance(message.channel, discord.Thread):
            return f"discord_thread_{message.channel.id}"
        return f"discord_channel_{message.channel.id}"
