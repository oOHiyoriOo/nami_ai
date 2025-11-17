# Quick Start Guide

Get up and running with the Personality Proxy API in 5 minutes.

## Prerequisites

- Python 3.12+
- Neo4j database (for memory system)
- Ollama (or OpenAI/Anthropic API key)

## Installation

### 1. Clone the Repository

```bash
git clone git@github.com:oOHiyoriOo/nami_ai.git
cd nami_ai
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Set Up Neo4j

**Option A: Docker**
```bash
docker run -d \
  --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/your_password \
  neo4j:latest
```

**Option B: Local Installation**
```bash
# Ubuntu/Debian
sudo apt install neo4j

# Start service
sudo systemctl start neo4j
```

Access Neo4j Browser at `http://localhost:7474` and set your password.

### 4. Set Up Ollama (Optional)

If using Ollama as your AI provider:

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull a model
ollama pull llama2
```

### 5. Configure

```bash
cp config.yml.example config.yml
nano config.yml
```

**Minimal configuration:**

```yaml
api:
  host: "0.0.0.0"
  port: 11434

ai_provider: ollama

providers:
  ollama:
    url: http://localhost:11434
    model: llama2
    system_prompt: nami

neo4j:
  uri: bolt://localhost:7687
  user: neo4j
  pass: your_password

memory_db:
  model: all-MiniLM-L6-v2

bot:
  log_level: INFO
```

### 6. Start the Server

```bash
python api_server_ollama.py
```

You should see:
```
[INFO] Personality Proxy API initialized!
[INFO] Provider: ollama
[INFO] Personality: nami
[INFO] Model: llama2
```

## First Request

### Using cURL

```bash
curl http://localhost:11434/api/chat -d '{
  "model": "llama2",
  "messages": [
    {"role": "user", "content": "Hello! Who are you?"}
  ],
  "user_id": "alice",
  "enable_memory": true,
  "enable_personality": true
}'
```

### Using Python

```python
from ollama import Client

client = Client(host='http://localhost:11434')

response = client.chat(
    model='llama2',
    messages=[
        {'role': 'user', 'content': 'Hello! Who are you?'}
    ],
    options={
        'user_id': 'alice',
        'enable_memory': True,
        'enable_personality': True
    }
)

print(response['message']['content'])
```

### Using Ollama CLI

```bash
export OLLAMA_HOST=http://localhost:11434
ollama run llama2
>>> Hello! Who are you?
```

## What's Next?

- **[Providers Guide](PROVIDERS.md)** - Switch to OpenAI, Anthropic, or add your own
- **[Memory System](MEMORY_SYSTEM.md)** - Understand how memories work
- **[API Reference](API_REFERENCE.md)** - Complete API documentation
- **[Tools](TOOLS.md)** - Add custom tools
- **[Personalities](../system_prompt/)** - Create custom personalities

## Troubleshooting

### Server won't start

**Error:** `Neo4j connection failed`
- Check Neo4j is running: `systemctl status neo4j` or `docker ps`
- Verify credentials in `config.yml`
- Test connection: `cypher-shell -a bolt://localhost:7687 -u neo4j -p your_password`

**Error:** `Ollama provider not available`
- Check Ollama is running: `ollama list`
- Verify URL in config.yml
- Test: `curl http://localhost:11434/api/tags`

**Error:** `Port already in use`
- Change port in `config.yml`: `api.port: 8080`
- Or stop other service using port 11434

### Memory not working

**No memories retrieved:**
- First conversation won't have memories (they're created as you chat)
- Check Neo4j has data: Open Neo4j Browser and run `MATCH (n) RETURN count(n)`
- Lower similarity threshold in code if needed

**Memories not being created:**
- Check logs in `logs/` directory
- Verify `enable_memory: true` in request
- Ensure vector_helper is working

### High latency

- **Disable memory** for faster responses: `enable_memory: false`
- **Reduce max_tool_calls** in config.yml: `max_tool_calls: 1`
- **Use local Ollama** instead of cloud APIs
- **Smaller model**: Use `llama2:7b` instead of larger models

## Common Commands

```bash
# Start server
python api_server_ollama.py

# Check health
curl http://localhost:11434/health

# List models
curl http://localhost:11434/api/tags

# View logs
tail -f logs/*.log

# Clear old logs
rm logs/*.log

# Restart with different config
python api_server_ollama.py --config my_config.yml
```

## Next Steps

1. **Customize your personality** - Edit `system_prompt/nami.md`
2. **Add tools** - Create custom tools in `OllamaTools/`
3. **Try different providers** - Switch to OpenAI or Anthropic
4. **Build your app** - Integrate with your application using the Ollama-compatible API

---

Need help? Check the [full documentation](../README.md) or [open an issue](https://github.com/oOHiyoriOo/nami_ai/issues).
