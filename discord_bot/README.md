# Nami AI Discord Bot

Discord bot client for the **Personality Proxy API** system. Available in three versions:

## 📚 Three Versions

### 1. **V2 - Discord Adapter (Recommended)**
**File:** `main_discord_bot_v2.py` | **[Docs](README_V2.md)**

Intelligent adapter that properly translates Discord's rich format:
- ✅ **Multi-user context** - Shows who's speaking with roles
- ✅ **Reply chains** - Preserves conversation threads
- ✅ **Rich content** - Handles embeds, attachments, reactions
- ✅ **User metadata** - Roles, join dates, server info
- ✅ **Channel context** - Thread awareness, server info

**Best for:** Most users. Gives AI full Discord context.

### 2. **Simple Bot**
**File:** `main_discord_bot_simple.py` | **[Docs](README_SIMPLE.md)**

Minimal thin client:
- ✅ Forwards messages to API
- ✅ Basic conversation history
- ❌ No rich Discord context
- ✅ 5 dependencies, fast startup

**Best for:** Testing, minimal footprint, simple deployments.

### 3. **Full Bot (Legacy)**
**File:** `main_discord_bot.py` | **[Setup Guide](SETUP.md)**

Full-featured with local memory and tools:
- ✅ All features
- ✅ Discord-specific tools (query messages, users, audit logs)
- ❌ Duplicates API functionality (memory, history)
- ❌ 17+ dependencies, larger footprint

**Best for:** Discord-specific tools, standalone operation.

## 🚀 Quick Start (V2 - Recommended)

### Prerequisites
1. **Discord Bot Token** - https://discord.com/developers/applications
2. **Personality Proxy API** - Must be running
3. **Python 3.12+**

### Installation

```bash
cd discord_bot

# Install dependencies
pip install -r requirements_simple.txt  # V2 uses same deps as simple

# Configure
cp config_simple.yml.example config.yml
nano config.yml
```

### Configuration

```yaml
dc:
  token: YOUR_DISCORD_BOT_TOKEN

ai_channel:
  - 1234567890  # Your channel ID

ollama:
  url: http://localhost:11434  # Personality Proxy API
  model: ollama/llama2

bot:
  log_level: INFO
```

### Discord Bot Setup

1. Create bot at https://discord.com/developers/applications
2. Enable **Message Content Intent** (required!)
3. Generate invite with scopes: `bot`, `applications.commands`
4. Permissions: Read Messages, Send Messages, Read History, Use Slash Commands

### Run

```bash
# Make sure Personality Proxy API is running!
python main_discord_bot_v2.py
```

## 📊 Version Comparison

| Feature | V2 Adapter | Simple | Full (Legacy) |
|---------|-----------|--------|---------------|
| **Multi-user context** | ✅ Rich | ❌ Basic | ❌ Basic |
| **Reply chains** | ✅ Preserved | ❌ Lost | ❌ Lost |
| **Rich content** | ✅ Described | ❌ Ignored | ❌ Ignored |
| **User metadata** | ✅ Roles, etc | ❌ ID only | ❌ ID only |
| **Discord tools** | ❌ | ❌ | ✅ |
| **Dependencies** | 5 packages | 5 packages | 17+ packages |
| **Memory/History** | Via API | Via API | Local + API |
| **Startup time** | Fast | Fast | Slow |
| **Footprint** | Small | Small | Large |

## 🎯 Which Version?

**Start with V2 Adapter:**
- Best Discord context for AI
- Same minimal dependencies as simple
- Recommended for 95% of use cases

**Use Simple if:**
- Just testing
- Don't care about Discord context
- Want absolutely minimal code

**Use Full if:**
- Need Discord-specific tools
- Want standalone operation
- Need direct database access

## 📖 Documentation

- **[V2 with Adapter](README_V2.md)** - Recommended, full Discord context
- **[Simple Bot](README_SIMPLE.md)** - Minimal thin client
- **[Full Bot Setup](SETUP.md)** - Legacy full-featured version
- **[Architecture Comparison](ARCHITECTURE.md)** - Detailed comparison

## 💡 Example: What AI Sees

### V2 Adapter (Recommended)
```
**Alice** (roles: Admin, Moderator) [ID: 123456]
[Replying to previous message(s):
  Bob: What do you think?
]

says: I agree with Bob!
attached:
  - 🖼️ Image: screenshot.png
reactions:
  👍 x10
```

### Simple/Full
```
Alice: I agree with Bob!
```

**V2 gives AI 10x better context!**

## 🔧 Commands

All versions include:
- `/toggle_ai` - Enable/disable in channel
- `/restart` - Restart bot (owner only)

Full version also has:
- `/amnesia` - Clear memory/history
- `/neo4j` - Query database
- `/debug` - System info

## 🌐 Architecture

```
Discord Users
     ↓
Discord Bot (V2 Adapter)
     ↓ Rich formatted messages
Personality Proxy API
     ↓
AI Provider + Memory + History
```

## 🐛 Troubleshooting

### Bot doesn't respond

1. Check API is running: `curl http://localhost:11434/health`
2. Verify Message Content Intent enabled
3. Check channel ID in `ai_channel` list
4. Verify bot has permissions

### AI doesn't understand context

Use V2 Adapter! Simple/Full lose Discord context.

### Check logs

```bash
# Enable debug logging
# config.yml
bot:
  log_level: DEBUG

# See formatted messages in logs/
tail -f logs/*.log
```

## 📦 Installation Summary

```bash
# Clone/extract discord_bot folder
cd discord_bot

# V2 Adapter (Recommended)
pip install -r requirements_simple.txt
cp config_simple.yml.example config.yml
# Edit config.yml
python main_discord_bot_v2.py

# Simple
pip install -r requirements_simple.txt
cp config_simple.yml.example config.yml
# Edit config.yml
python main_discord_bot_simple.py

# Full (Legacy)
pip install -r requirements.txt
cp config.yml.example config.yml
# Edit config.yml - needs Neo4j, etc.
python main_discord_bot.py
```

## 🚀 Deployment

All versions support:
- Docker deployment
- systemd services
- Multiple bot instances (share same API)

See SETUP.md for detailed deployment instructions.

## 📄 License

MIT License - Part of Nami AI project

---

**Recommended:** Start with `main_discord_bot_v2.py` for best results!

**Questions?** Check the documentation:
- [V2 Adapter Guide](README_V2.md) - Detailed features and examples
- [Architecture Guide](ARCHITECTURE.md) - Comparison and when to use each
- [Setup Guide](SETUP.md) - Deployment and troubleshooting

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
