# Discord Bot Setup Guide

This guide will help you extract and set up the Discord bot as a standalone project.

## Extraction Steps

### 1. Copy the discord_bot folder

```bash
# From the main nami_ai project
cp -r discord_bot /path/to/your/new/location/nami-discord-bot
cd /path/to/your/new/location/nami-discord-bot
```

### 2. Initialize as standalone project (optional)

```bash
# Initialize git repository
git init

# Add all files
git add .

# Initial commit
git commit -m "Initial commit: Nami AI Discord Bot"
```

## Dependencies

### Python Environment

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Linux/Mac:
source venv/bin/activate
# On Windows:
# venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### External Services

**Required:**
1. **Discord Application** - Bot token from https://discord.com/developers
2. **Personality Proxy API** - Must be running separately
3. **Neo4j Database** - For memory system

**Optional:**
1. **Brave Search API** - For web search tool
2. **ComfyUI Server** - For image generation

## Configuration

### 1. Create config.yml

```bash
cp config.yml.example config.yml
```

### 2. Edit config.yml

**Essential settings:**

```yaml
dc:
  token: "YOUR_DISCORD_BOT_TOKEN_HERE"
  sync_guild: -1  # or your test guild ID

ai_channel:
  - 1234567890  # Replace with actual channel IDs

ollama:
  url: http://localhost:11434  # Your Personality Proxy API URL
  model: ollama/llama2  # Format: <provider>/<model>

neo4j:
  uri: bolt://localhost:7687
  user: neo4j
  pass: "YOUR_NEO4J_PASSWORD"
```

### 3. Configure Discord Bot

1. Go to https://discord.com/developers/applications
2. Create new application or select existing
3. Go to "Bot" section
4. Enable **Privileged Gateway Intents**:
   - ✅ Presence Intent
   - ✅ Server Members Intent
   - ✅ Message Content Intent (REQUIRED)
5. Copy bot token to config.yml
6. Save changes

### 4. Generate Invite Link

1. In Discord Developer Portal → "OAuth2" → "URL Generator"
2. Select scopes:
   - ✅ `bot`
   - ✅ `applications.commands`
3. Select bot permissions:
   - ✅ Read Messages/View Channels
   - ✅ Send Messages
   - ✅ Send Messages in Threads
   - ✅ Embed Links
   - ✅ Attach Files
   - ✅ Read Message History
   - ✅ Use Slash Commands
   - ✅ Use External Emojis (optional)
4. Copy generated URL and invite bot to your server

## Running the Bot

### Start Required Services

**1. Neo4j Database:**
```bash
# Docker example
docker run -d \
  --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/your_password \
  neo4j:latest
```

**2. Personality Proxy API:**
```bash
# In the main nami_ai project
python api_server.py
```

**3. Discord Bot:**
```bash
# In the discord_bot directory
python main_discord_bot.py
```

### Verify Bot is Running

You should see:
```
Logged in as YourBotName (123456789)
Synced commands globally.
```

## Testing

### 1. Check Bot Status

In Discord, use:
```
/debug
```

Should show system information and memory stats.

### 2. Test AI Response

In a configured AI channel, send a message:
```
Hello bot!
```

Bot should respond using the configured personality.

### 3. Test Memory

```
User: Remember that I like Python programming
Bot: [responds and stores memory]

# Later...
User: What do you know about me?
Bot: [recalls that you like Python programming]
```

## Project Structure

```
nami-discord-bot/
├── main_discord_bot.py      # Entry point - START HERE
├── config.yml               # Your configuration (gitignored)
├── config.yml.example       # Configuration template
├── requirements.txt         # Python dependencies
├── README.md               # Main documentation
├── SETUP.md                # This file
│
├── commands/               # Discord slash commands
├── events/                 # Discord event handlers
├── OllamaTools/           # Discord-specific AI tools
├── lib/                   # Core libraries
├── system_prompt/         # Personality definitions
└── logs/                  # Log files (created on first run)
```

## Customization

### Change Personality

Edit `config.yml`:
```yaml
ollama:
  system_prompt: ahri  # Uses system_prompt/ahri.md
```

Or create your own in `system_prompt/custom.md`

### Change Model

Edit `config.yml`:
```yaml
ollama:
  model: copilot/gpt-4.1  # Use GitHub Copilot
  # model: openai/gpt-4   # Use OpenAI
  # model: ollama/mistral # Use local Mistral
```

Note: The Personality Proxy API must have the provider configured.

### Add Commands

Create file in `commands/`:
```python
# commands/hello.py
from discord import app_commands
import discord

class Command:
    def __init__(self, client, cfg):
        @client.tree.command(name="hello", description="Say hello")
        async def hello(interaction: discord.Interaction):
            await interaction.response.send_message("Hello!")
```

Command will be automatically loaded on bot start.

## Troubleshooting

### Bot doesn't respond to messages

**Check:**
- [ ] Message Content Intent enabled in Discord Developer Portal
- [ ] Channel ID is in `ai_channel` list in config
- [ ] Bot has permissions to read/send messages
- [ ] Personality Proxy API is running and accessible

**Fix:**
```bash
# Check API is running
curl http://localhost:11434/health

# Check logs
tail -f logs/*.log
```

### Commands don't appear

**Check:**
- [ ] Bot has `applications.commands` scope
- [ ] `sync_guild` is set correctly
- [ ] Bot has been restarted after adding commands

**Fix:**
```yaml
# Try syncing to a specific guild for faster testing
dc:
  sync_guild: 123456789  # Your guild ID
```

### Memory not working

**Check:**
- [ ] Neo4j is running and accessible
- [ ] Credentials in config are correct
- [ ] Neo4j URI format is correct (bolt://...)

**Fix:**
```bash
# Test Neo4j connection
docker logs neo4j

# Check config
grep -A3 "neo4j:" config.yml
```

### API connection errors

**Check:**
- [ ] Personality Proxy API is running
- [ ] URL in config is correct
- [ ] Model format is correct (`provider/model`)
- [ ] Firewall allows connection

**Fix:**
```bash
# Test API connection
curl http://localhost:11434/api/version

# Check model is available
curl http://localhost:11434/api/tags
```

## Deployment

### Production Checklist

- [ ] Use environment variables for secrets
- [ ] Set up log rotation
- [ ] Configure systemd service (Linux) or equivalent
- [ ] Set up monitoring/alerts
- [ ] Regular database backups
- [ ] Rate limiting configuration
- [ ] Error handling verification

### Example systemd service

```ini
[Unit]
Description=Nami Discord Bot
After=network.target neo4j.service

[Service]
Type=simple
User=botuser
WorkingDirectory=/path/to/nami-discord-bot
ExecStart=/path/to/venv/bin/python main_discord_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Docker Deployment (Optional)

Create `Dockerfile`:
```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main_discord_bot.py"]
```

Create `docker-compose.yml`:
```yaml
version: '3.8'

services:
  discord-bot:
    build: .
    volumes:
      - ./config.yml:/app/config.yml
      - ./logs:/app/logs
    depends_on:
      - neo4j
    restart: unless-stopped

  neo4j:
    image: neo4j:latest
    environment:
      - NEO4J_AUTH=neo4j/password
    ports:
      - "7474:7474"
      - "7687:7687"
    volumes:
      - neo4j_data:/data

volumes:
  neo4j_data:
```

Run:
```bash
docker-compose up -d
```

## Migration Notes

### Differences from Main Project

1. **No API Server** - This is a client only
2. **No Provider System** - Uses API endpoint instead
3. **Discord-Specific** - Includes Discord tools and events
4. **Simplified** - Focuses on bot functionality

### Shared Components

Both projects can share:
- Neo4j database (same instance)
- System prompts (same personality files)
- Memory system (same structure)

### Independent Running

The bot and API can run:
- On same machine (localhost)
- On different machines (configure URLs)
- In containers (Docker/Kubernetes)

## Getting Help

1. Check logs in `logs/` directory
2. Enable DEBUG logging in config
3. Review README.md for detailed documentation
4. Check main project documentation
5. Verify all services are running

## Next Steps

After setup:
1. Test basic functionality
2. Customize personality
3. Configure tools you want to use
4. Add custom commands
5. Set up monitoring
6. Plan deployment strategy

---

**Ready to run?**
```bash
python main_discord_bot.py
```

Good luck! 🚀
