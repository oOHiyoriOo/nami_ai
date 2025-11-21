"""
Simplified Discord Bot for Personality Proxy API
This bot is a thin client that forwards messages to the API.
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
    """Handle incoming messages."""
    # Ignore bot's own messages
    if message.author.bot:
        return

    # Check if in AI channel
    if message.channel.id not in ai_channels:
        return

    # Show typing indicator
    async with message.channel.typing():
        try:
            # Prepare conversation history from recent messages
            messages = []

            # Get recent messages for context (last 10)
            async for msg in message.channel.history(limit=10, before=message):
                if not msg.author.bot:
                    messages.insert(0, {
                        "role": "user",
                        "content": f"{msg.author.display_name}: {msg.content}"
                    })
                elif msg.author == client.user:
                    messages.insert(0, {
                        "role": "assistant",
                        "content": msg.content
                    })

            # Add current message
            messages.append({
                "role": "user",
                "content": f"{message.author.display_name}: {message.content}"
            })

            # Call Personality Proxy API
            response = await api_client.chat(
                model=cfg.data['ollama']['model'],
                messages=messages,
                options={
                    'user_id': str(message.author.id),
                    'conversation_id': f"discord_{message.channel.id}",
                    'enable_memory': True,
                    'enable_personality': True
                }
            )

            # Send response
            reply = response['message']['content']

            # Split long messages (Discord limit is 2000 chars)
            if len(reply) > 2000:
                chunks = [reply[i:i+2000] for i in range(0, len(reply), 2000)]
                for chunk in chunks:
                    await message.channel.send(chunk)
            else:
                await message.channel.send(reply)

        except Exception as e:
            logging.error(f"Error processing message: {e}", exc_info=True)
            await message.channel.send(f"❌ Error: {str(e)}")


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
