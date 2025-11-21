"""
Discord Bot for Personality Proxy API - V2 with Discord Adapter
Properly translates Discord's rich message format for AI understanding.
"""
import logging
import os
import time
import discord
import asyncio
from colorama import Fore, init
from discord import app_commands
from ollama import AsyncClient

from lib.global_registry import g_data
from lib.load_commands import load_commands
from lib.configurationFile import ConfigurationFile
from lib.discord_adapter import DiscordMessageAdapter

init(convert=True, autoreset=True)

# --- Logging Configuration ---
os.makedirs('./logs', exist_ok=True)

cfg_temp = ConfigurationFile("config.yml")
log_level_str = cfg_temp.data.get('bot', {}).get('log_level', 'INFO')
log_level = getattr(logging, str(log_level_str).upper(), logging.INFO)

logging.basicConfig(
    level=log_level,
    format=(f'[%(asctime)s] {Fore.YELLOW} {"[%(levelname)s]":<8} {Fore.RESET} [%(name)s] %(message)s'),
    handlers=[
        logging.FileHandler(f"./logs/{time.strftime('%Y-%m-%d_%H_%M_%S')}.log", 'w', 'utf-8'),
        logging.StreamHandler()
    ],
    force=True
)
logging.info(f"Logging configured with level {logging.getLevelName(log_level)}.")

# --- Configuration ---
cfg = g_data.get_or_create("cfg", ConfigurationFile, "config.yml")

# Discord client setup
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.members = True

client = g_data.get_or_create("client", discord.Client, intents=intents)
tree_instance = app_commands.CommandTree(client)
client.tree = tree_instance

# Discord adapter for message formatting
adapter = DiscordMessageAdapter(client)
g_data.set("adapter", adapter)

# Ollama API client (points to Personality Proxy API)
api_url = cfg.data['ollama']['url']
api_client = AsyncClient(host=api_url)
g_data.set("api_client", api_client)

# AI channels configuration
ai_channels = set(cfg.data.get('ai_channel', []))
g_data.set("ai_channels", ai_channels)


@client.event
async def on_ready():
    """Bot ready event."""
    logging.info("="*70)
    logging.info(f"{Fore.GREEN}Discord Bot Ready!")
    logging.info(f"Bot: {client.user.name} ({client.user.id})")
    logging.info(f"API: {api_url}")
    logging.info(f"Model: {cfg.data['ollama']['model']}")
    logging.info(f"AI Channels: {len(ai_channels)}")
    logging.info("="*70)

    # Sync slash commands
    sync_guild_id = cfg.data['dc']['sync_guild']
    if sync_guild_id == -1:
        await client.tree.sync()
        logging.info("Synced commands globally.")
    else:
        await client.tree.sync(guild=discord.Object(id=sync_guild_id))
        logging.info(f"Synced commands to guild {sync_guild_id}.")


@client.event
async def on_message(message: discord.Message):
    """Handle incoming messages with rich Discord context."""
    # Ignore bot's own messages
    if message.author.bot:
        return

    # Check if in AI channel
    if message.channel.id not in ai_channels:
        return

    # Show typing indicator
    async with message.channel.typing():
        try:
            # Use adapter to get properly formatted conversation context
            discord_messages = await adapter.get_conversation_context(
                message.channel,
                message,
                limit=10
            )

            # Add Discord channel context as system message
            channel_context = build_channel_context(message)

            # Format messages for API
            api_messages = adapter.format_for_api(
                discord_messages,
                system_context=channel_context
            )

            # Extract IDs for API tracking
            user_id = adapter.extract_user_id_for_api(message)
            conversation_id = adapter.extract_conversation_id_for_api(message)

            logging.debug(f"Sending {len(api_messages)} messages to API")
            logging.debug(f"User ID: {user_id}, Conversation ID: {conversation_id}")

            # Call Personality Proxy API
            response = await api_client.chat(
                model=cfg.data['ollama']['model'],
                messages=api_messages,
                options={
                    'user_id': user_id,
                    'conversation_id': conversation_id,
                    'enable_memory': True,
                    'enable_personality': True
                }
            )

            # Log returned conversation_id for debugging
            returned_conv_id = response.get('conversation_id')
            if returned_conv_id:
                logging.debug(f"API returned conversation_id: {returned_conv_id}")

            # Send response
            reply = response['message']['content']

            # Handle mentions in response
            reply = format_response_for_discord(reply, message)

            # Split long messages (Discord limit is 2000 chars)
            if len(reply) > 2000:
                chunks = [reply[i:i+1900] for i in range(0, len(reply), 1900)]
                for chunk in chunks:
                    await message.channel.send(chunk)
            else:
                await message.channel.send(reply)

        except Exception as e:
            logging.error(f"Error processing message: {e}", exc_info=True)
            await message.channel.send(f"❌ Error: {str(e)}")


def build_channel_context(message: discord.Message) -> str:
    """Build Discord channel context for AI."""
    parts = []

    # Channel info
    if isinstance(message.channel, discord.Thread):
        parts.append(f"[Discord Thread: #{message.channel.name}")
        if message.channel.parent:
            parts.append(f"in #{message.channel.parent.name}")
    else:
        parts.append(f"[Discord Channel: #{message.channel.name}")

    # Server info
    if message.guild:
        parts.append(f"on server '{message.guild.name}'")
        parts.append(f"with {message.guild.member_count} members")

    parts.append("]")

    context = " ".join(parts)

    # Add note about message format
    context += "\n\nNote: Messages show user context including display names, roles, and may include attachments, embeds, reactions, or reply chains."

    return context


def format_response_for_discord(response: str, message: discord.Message) -> str:
    """Format AI response for Discord."""
    # Note: Could add logic here to:
    # - Convert @mentions to actual Discord mentions
    # - Format code blocks with proper Discord syntax
    # - Add Discord emojis
    # For now, just return as-is
    return response


async def main():
    """Main bot initialization."""
    logging.info("Initializing Discord bot...")

    # Load commands (for bot management)
    await load_commands(client, cfg)
    logging.info("Commands loaded.")

    # Start Discord client
    logging.info("Starting Discord client...")
    await client.start(cfg.data['dc']['token'])


if __name__ == "__main__":
    asyncio.run(main())
