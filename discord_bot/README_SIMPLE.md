# Nami AI Discord Bot (Simplified)

A **thin client** Discord bot that connects to the Personality Proxy API. All AI processing, memory, and context management happens in the API.

## Architecture

```
Discord User → Discord Bot → Personality Proxy API → AI Provider
                                      ↓
                              Memory & History (handled by API)
```

**The bot only:**
- Receives Discord messages
- Forwards them to the API with user context
- Returns API responses to Discord

**The API handles:**
- AI model selection and switching
- Memory storage and retrieval
- Conversation history
- Context building with personality
- Tool execution

## Prerequisites

1. **Discord Bot Token** - Create at https://discord.com/developers/applications
2. **Personality Proxy API** - Must be running (see main project)
3. **Python 3.12+**

**You do NOT need:**
- ❌ Neo4j database (API handles it)
- ❌ SQLite database (API handles it)
- ❌ Embedding models (API handles it)
- ❌ LangChain/vector libraries (API handles it)

## Quick Start

### 1. Installation

```bash
cd discord_bot

# Install minimal dependencies
pip install -r requirements_simple.txt
```

### 2. Configuration

```bash
cp config_simple.yml.example config.yml
nano config.yml
```

**Required settings:**
```yaml
dc:
  token: YOUR_DISCORD_BOT_TOKEN

ai_channel:
  - 1234567890  # Your channel ID

ollama:
  url: http://localhost:11434  # Your Personality Proxy API
  model: ollama/llama2  # Format: provider/model
```

### 3. Discord Bot Setup

1. Create bot at https://discord.com/developers/applications
2. Enable **Message Content Intent** (required!)
3. Generate invite URL with these scopes: `bot`, `applications.commands`
4. Permissions needed:
   - Read Messages/View Channels
   - Send Messages
   - Read Message History
   - Use Slash Commands

### 4. Run

```bash
# Make sure Personality Proxy API is running first!
python main_discord_bot_simple.py
```

## How It Works

### Message Flow

1. **User sends message** in configured channel
2. **Bot collects context** (last 10 messages for conversation flow)
3. **Bot calls API** with:
   - Messages history
   - User ID (for memory personalization)
   - Conversation ID (channel-specific context)
4. **API processes** (personality, memory, AI model)
5. **Bot receives response** from API
6. **Bot sends reply** in Discord

### What Gets Sent to API

```python
{
  "model": "ollama/llama2",
  "messages": [
    {"role": "user", "content": "Previous messages..."},
    {"role": "assistant", "content": "Previous responses..."},
    {"role": "user", "content": "Current message"}
  ],
  "options": {
    "user_id": "123456789",  # Discord user ID
    "conversation_id": "discord_987654321",  # Discord channel ID
    "enable_memory": True,
    "enable_personality": True
  }
}
```

The API uses `user_id` to:
- Store and retrieve user-specific memories
- Track conversation history per user
- Personalize responses

The API uses `conversation_id` to:
- Keep channel conversations separate
- Provide channel-specific context

## Configuration

### Model Selection

Change which AI model the API uses:

```yaml
ollama:
  model: ollama/llama2      # Local Ollama
  # model: copilot/gpt-4.1  # GitHub Copilot
  # model: openai/gpt-4     # OpenAI
```

**Note:** The model must be configured in the Personality Proxy API.

### AI Channels

Configure which channels the bot responds in:

```yaml
ai_channel:
  - 1234567890
  - 9876543210
```

Get channel ID: Right-click channel → Copy Channel ID (requires Developer Mode)

### Personality

Personality is configured **on the API side**, not in the bot. To change personality:

1. Edit the API's `config.yml`
2. Change `default_system_prompt` setting
3. Restart the API

```yaml
# In API config.yml
default_system_prompt: nami  # or ahri, ranni, etc.
```

## Commands

The bot includes basic management commands:

- `/toggle_ai` - Enable/disable bot in current channel
- `/restart` - Restart the bot (owner only)

**Note:** Memory-related commands (`/amnesia`, `/neo4j`) should be handled by the API or removed since the bot doesn't have direct database access.

## Comparison: Old vs New

### Old Architecture (Redundant)
```
Discord Bot:
  ├── Discord connection
  ├── Neo4j memory database ❌ DUPLICATE
  ├── SQLite history database ❌ DUPLICATE
  ├── Context builder ❌ DUPLICATE
  ├── Personality loader ❌ DUPLICATE
  └── Call Ollama API

API Server:
  ├── Neo4j memory database
  ├── SQLite history database
  ├── Context builder
  ├── Personality loader
  └── AI Provider
```

### New Architecture (Simplified)
```
Discord Bot:
  ├── Discord connection
  └── Call Personality Proxy API ✅

API Server:
  ├── Neo4j memory database ✅
  ├── Context builder ✅
  ├── Personality loader ✅
  └── AI Provider ✅
```

## Files

**Simplified bot uses:**
- `main_discord_bot_simple.py` - Main bot (170 lines vs 116 lines)
- `config_simple.yml.example` - Minimal config
- `requirements_simple.txt` - 5 dependencies vs 15+
- `lib/configurationFile.py` - Config loader
- `lib/global_registry.py` - Shared state
- `lib/load_commands.py` - Command loader
- `commands/` - Management commands only

**Not needed anymore:**
- ❌ `lib/memory_db.py` - API handles memory
- ❌ `lib/asyncsqlite.py` - API handles history
- ❌ `lib/chat_helper.py` - API handles chat
- ❌ `lib/ollama_helper.py` - Using simple ollama client
- ❌ `lib/tool_loader.py` - API handles tools
- ❌ `system_prompt/` - API handles personalities
- ❌ `OllamaTools/` - API handles tool execution

## Troubleshooting

### Bot doesn't respond

**Check:**
1. Personality Proxy API is running
   ```bash
   curl http://localhost:11434/health
   ```

2. Channel ID is in `ai_channel` list

3. Message Content Intent is enabled

4. Bot has permissions

### API errors

**Check API logs:**
```bash
# In API directory
tail -f logs/api_*.log
```

**Test API directly:**
```bash
curl http://localhost:11434/api/chat -d '{
  "model": "ollama/llama2",
  "messages": [{"role": "user", "content": "test"}]
}'
```

### Connection refused

**Check:**
- API is running on correct host/port
- Firewall allows connection
- URL in config is correct

## Development

### Adding Management Commands

Create file in `commands/`:

```python
# commands/status.py
from discord import app_commands
import discord

class Command:
    def __init__(self, client, cfg):
        @client.tree.command(name="status", description="Bot status")
        async def status(interaction: discord.Interaction):
            api_url = cfg.data['ollama']['url']
            model = cfg.data['ollama']['model']
            await interaction.response.send_message(
                f"✅ Connected to {api_url}\nModel: {model}"
            )
```

## Migration from Full Bot

If you were using the full Discord bot with local memory:

1. **Data is preserved** - Memory stays in Neo4j, accessible via API
2. **No migration needed** - API uses same database
3. **Switch bot file** - Use `main_discord_bot_simple.py`
4. **Update config** - Use simplified config
5. **Remove dependencies** - Install `requirements_simple.txt`

## Deployment

### Running Both Services

**Terminal 1 - API Server:**
```bash
cd /path/to/nami_ai
python api_server.py
```

**Terminal 2 - Discord Bot:**
```bash
cd /path/to/discord_bot
python main_discord_bot_simple.py
```

### Docker Compose Example

```yaml
version: '3.8'

services:
  api:
    build: ../  # Main project
    ports:
      - "11434:11434"
    environment:
      - NEO4J_URI=bolt://neo4j:7687
    depends_on:
      - neo4j

  discord-bot:
    build: .
    depends_on:
      - api
    environment:
      - OLLAMA_URL=http://api:11434

  neo4j:
    image: neo4j:latest
    environment:
      - NEO4J_AUTH=neo4j/password
    ports:
      - "7474:7474"
      - "7687:7687"
```

## Why Simplify?

**Benefits:**
- ✅ Single source of truth for memory/context
- ✅ Easier to maintain (less code duplication)
- ✅ Faster bot startup (no DB initialization)
- ✅ Smaller dependencies (5 vs 15+ packages)
- ✅ Better separation of concerns
- ✅ Can run multiple bot clients with same memory

**Use simplified bot when:**
- You have the Personality Proxy API running
- You want minimal bot footprint
- You don't need Discord-specific tools

**Use full bot when:**
- You need Discord-specific tools in AI responses
- You want standalone operation (no API)
- You're not using the API system

## License

MIT License - Part of the Nami AI project

---

**Quick Start:**
```bash
pip install -r requirements_simple.txt
cp config_simple.yml.example config.yml
# Edit config.yml with your Discord token and channel IDs
python main_discord_bot_simple.py
```

**Remember:** Start the Personality Proxy API first!
