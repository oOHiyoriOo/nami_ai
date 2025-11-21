# Nami AI Discord Bot

A Discord bot that connects to the **Personality Proxy API** to provide AI-powered conversations with personality, memory, and tool integration.

## Overview

This Discord bot is a client for the Personality Proxy API system. It provides:
- 🎭 **Personality-driven conversations** using system prompts
- 💾 **Long-term memory** via Neo4j memory database
- 🔍 **Discord-specific tools** (query messages, users, audit logs)
- 📝 **Conversation history** tracking
- 🎨 **Image generation** via ComfyUI (optional)
- 🌐 **Web search** capabilities (optional)

## Architecture

```
Discord Bot → Personality Proxy API → AI Provider (Ollama/OpenAI/Copilot)
     ↓
Neo4j Memory Database
     ↓
SQLite History Database
```

## Prerequisites

1. **Discord Bot Token** - Create a bot at https://discord.com/developers/applications
2. **Personality Proxy API** - Running instance of the API server (see main project)
3. **Neo4j Database** - For long-term memory
4. **Python 3.12+**

## Quick Start

### 1. Installation

```bash
# Clone or extract this discord_bot folder
cd discord_bot

# Install dependencies
pip install -r requirements.txt
```

### 2. Configuration

```bash
# Copy config template
cp config.yml.example config.yml

# Edit configuration
nano config.yml
```

**Required settings:**
- `dc.token` - Your Discord bot token
- `ollama.url` - URL of your Personality Proxy API (default: http://localhost:11434)
- `ollama.model` - Model to use (format: `<provider>/<model>`, e.g., `ollama/llama2`)
- `neo4j.*` - Neo4j database credentials
- `ai_channel` - List of Discord channel IDs where the bot responds

### 3. Discord Bot Setup

1. Go to https://discord.com/developers/applications
2. Create a new application
3. Go to "Bot" section and create a bot
4. Enable **Message Content Intent** under Privileged Gateway Intents
5. Copy the bot token to `config.yml`
6. Generate invite URL:
   - Go to "OAuth2" → "URL Generator"
   - Select scopes: `bot`, `applications.commands`
   - Select bot permissions:
     - Read Messages/View Channels
     - Send Messages
     - Read Message History
     - Attach Files
     - Use Slash Commands
   - Copy and use the generated URL to invite the bot

### 4. Run the Bot

```bash
python main_discord_bot.py
```

## Configuration Details

### Model Selection

Use the `<provider>/<model>` format to specify which AI backend to use:

```yaml
ollama:
  model: ollama/llama2          # Local Ollama
  # model: copilot/gpt-4.1      # GitHub Copilot
  # model: openai/gpt-4         # OpenAI
```

The Personality Proxy API must have the corresponding provider configured.

### Personality

Change the bot's personality by editing the `system_prompt` setting:

```yaml
ollama:
  system_prompt: nami  # Uses system_prompt/nami.md
```

Available personalities in `system_prompt/`:
- `nami` - Friendly AI assistant
- `ahri` - Playful and charismatic
- `ranni` - Mysterious and wise
- `tars` - Robotic and honest
- Create your own by adding a `.md` file!

### AI Channels

Configure which channels the bot responds in:

```yaml
ai_channel:
  - 1234567890  # Channel ID 1
  - 9876543210  # Channel ID 2
```

To get a channel ID: Right-click channel → Copy Channel ID (requires Developer Mode enabled in Discord settings)

## Commands

The bot includes several slash commands:

### `/debug`
Display system information and memory statistics.

```
/debug
```

### `/neo4j`
Query the Neo4j memory database directly.

```
/neo4j query:"MATCH (n) RETURN n LIMIT 10"
```

### `/amnesia`
Clear conversation history and/or memory for the current user or channel.

```
/amnesia type:user    # Clear your user history
/amnesia type:channel # Clear channel history
```

### `/toggle_ai`
Enable/disable AI responses in the current channel.

```
/toggle_ai
```

### `/restart`
Restart the bot (owner only).

```
/restart
```

## Discord Tools

The bot includes Discord-specific tools that can be used in AI conversations:

### `query_discord_message`
Search for messages in Discord channels.

```
User: "Find messages about Python in this channel"
```

### `query_discord_user`
Look up information about Discord users.

```
User: "Who is @username?"
```

### `query_audit_log`
Query server audit logs (requires permissions).

```
User: "Show recent bans in this server"
```

## Events

The bot monitors Discord events in the `events/` folder:

- `on_message_ai_response.py` - Handles message responses with AI

## Project Structure

```
discord_bot/
├── main_discord_bot.py         # Main bot entry point
├── config.yml.example          # Configuration template
├── requirements.txt            # Python dependencies
├── README.md                   # This file
│
├── commands/                   # Slash commands
│   ├── amnesia.py             # Clear history/memory
│   ├── debug.py               # System debug info
│   ├── neo4j.py               # Database queries
│   ├── restart.py             # Restart bot
│   └── toggle_ai.py           # Toggle AI responses
│
├── events/                     # Discord event handlers
│   └── on_message_ai_response.py  # Message handling
│
├── lib/                        # Core libraries
│   ├── chat_helper.py         # Chat utilities
│   ├── memory_db.py           # Neo4j interface
│   ├── ollama_helper.py       # API client
│   ├── load_commands.py       # Command loader
│   ├── load_events.py         # Event loader
│   ├── global_registry.py     # Shared state
│   └── ...                    # Other utilities
│
├── OllamaTools/               # Discord-specific tools
│   ├── query_discord_message.py
│   ├── query_discord_user.py
│   └── query_audit_log.py
│
└── system_prompt/             # Personality definitions
    ├── nami.md
    ├── ahri.md
    └── ...
```

## Connecting to Personality Proxy API

The bot uses the Ollama-compatible API format. Make sure your Personality Proxy API is running:

```bash
# In the main project directory
python api_server.py
```

The bot will connect to the API endpoint specified in `config.yml`:

```yaml
ollama:
  url: http://localhost:11434
```

## Memory System

The bot uses Neo4j for long-term memory:

1. **Episodic Memory** - Remembers conversations and events
2. **Knowledge Memory** - Stores facts and information
3. **Procedural Memory** - Tracks tasks and processes

Configure Neo4j connection in `config.yml`:

```yaml
neo4j:
  uri: bolt://localhost:7687
  user: neo4j
  pass: your_password
```

## Optional Tools

### Web Search

Requires Brave Search API key:

```yaml
bot:
  brave_search_token: YOUR_BRAVE_API_KEY
```

### Image Generation (ComfyUI)

Requires ComfyUI server:

```yaml
comfyui:
  server: localhost:8188
  workflow: lunarcherry.json
```

## Development

### Adding Commands

Create a new file in `commands/`:

```python
# commands/mycommand.py
from discord import app_commands
import discord

class Command:
    def __init__(self, client, cfg):
        @client.tree.command(name="mycommand", description="My custom command")
        async def mycommand(interaction: discord.Interaction):
            await interaction.response.send_message("Hello!")
```

### Adding Events

Create a new file in `events/`:

```python
# events/my_event.py
import discord

async def setup(client, cfg):
    @client.event
    async def on_member_join(member):
        print(f"{member.name} joined the server!")
```

## Troubleshooting

### Bot doesn't respond
1. Check `ai_channel` configuration includes the channel ID
2. Verify Personality Proxy API is running
3. Check bot has Message Content Intent enabled
4. Ensure bot has permission to read/send messages

### Memory not working
1. Verify Neo4j is running and accessible
2. Check Neo4j credentials in config
3. Review logs for connection errors

### Commands not appearing
1. Check `sync_guild` setting in config
2. Try setting to specific guild ID for faster testing
3. Bot needs "applications.commands" scope

## Logs

Logs are stored in `logs/` directory with timestamp filenames.

Set log level in config:
```yaml
bot:
  log_level: DEBUG  # DEBUG, INFO, WARNING, ERROR, CRITICAL
```

## Migration from Main Project

This bot is extracted from the main Nami AI project. To migrate:

1. Install Personality Proxy API separately
2. Configure this bot to point to the API
3. Update channel IDs and Discord token
4. Run both the API server and this bot

## Support

For issues and questions:
- Check the main project documentation
- Review configuration examples
- Enable DEBUG logging for detailed information

## License

MIT License - Part of the Nami AI project

---

**Quick Start Checklist:**
- [ ] Install dependencies (`pip install -r requirements.txt`)
- [ ] Copy `config.yml.example` to `config.yml`
- [ ] Add Discord bot token
- [ ] Configure Neo4j connection
- [ ] Set AI channel IDs
- [ ] Start Personality Proxy API
- [ ] Run bot (`python main_discord_bot.py`)
