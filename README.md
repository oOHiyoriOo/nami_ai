# Personality Proxy API

An **Ollama-compatible** personality proxy system with pluggable AI backends. This system adds personality, long-term memory, and tools to any AI provider while maintaining compatibility with the Ollama API format.

## Features

- 🔌 **Ollama-Compatible API** - Drop-in replacement for Ollama with personality enhancement
- 🧠 **Multiple AI Backends** - Easily switch between Ollama, OpenAI, Anthropic, and more
- 💾 **Neo4j Memory System** - Long-term memory (episodic, knowledge, procedural)
- 🎭 **Personality Management** - Markdown-based character definitions
- 🛠️ **Tool Integration** - Web search, memory queries, and custom tools
- 📝 **Conversation History** - SQLite-based conversation tracking
- 🔒 **Privacy-Focused** - Runs locally, your data stays on your machine

## Architecture

```
Client (Ollama-compatible) → Personality Proxy API
                                    ├── Provider Layer (Pluggable)
                                    │   ├── Ollama Provider
                                    │   ├── OpenAI Provider
                                    │   └── Anthropic Provider (add your own!)
                                    ├── Neo4j (Memory DB)
                                    ├── SQLite (Conversation History)
                                    └── Tools System
```

## Quick Start

### 1. Installation

```bash
git clone git@github.com:oOHiyoriOo/nami_ai.git
cd nami_ai
pip install -r requirements.txt
```

### 2. Configuration

Copy and edit the configuration file:

```bash
cp config.yml.example config.yml
# Edit config.yml with your settings
```

**Minimal configuration:**

```yaml
api:
  host: "0.0.0.0"
  port: 11434  # Ollama default port

ai_provider: ollama  # Choose: ollama, openai

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
```

### 3. Run the Server

```bash
python api_server_ollama.py
```

The API will be available at `http://localhost:11434` (compatible with Ollama clients).

### 4. Use with Any Ollama Client

The API is compatible with the Ollama API, so you can use it with any existing Ollama client:

**Command line (ollama CLI):**
```bash
# Set the API endpoint
export OLLAMA_HOST=http://localhost:11434

# Use as normal
ollama run llama2
```

**Python (ollama library):**
```python
from ollama import Client

client = Client(host='http://localhost:11434')

response = client.chat(
    model='llama2',
    messages=[
        {'role': 'user', 'content': 'Hello!'}
    ],
    # Optional: personality proxy extensions
    options={
        'user_id': 'alice',
        'enable_memory': True,
        'enable_personality': True
    }
)

print(response['message']['content'])
```

**cURL:**
```bash
curl http://localhost:11434/api/chat -d '{
  "model": "llama2",
  "messages": [
    {"role": "user", "content": "Hello!"}
  ],
  "user_id": "alice",
  "enable_memory": true
}'
```

## Switching AI Providers

The system supports multiple AI backends. Simply change the `ai_provider` in `config.yml`:

### Use Ollama (Local)

```yaml
ai_provider: ollama

providers:
  ollama:
    url: http://localhost:11434
    model: llama2
    system_prompt: nami
```

### Use OpenAI

```bash
# Install OpenAI library
pip install openai
```

```yaml
ai_provider: openai

providers:
  openai:
    api_key: sk-your-api-key
    model: gpt-4
    system_prompt: nami
```

### Use Anthropic Claude

```bash
# Install Anthropic library (when provider is implemented)
pip install anthropic
```

```yaml
ai_provider: anthropic

providers:
  anthropic:
    api_key: your-api-key
    model: claude-3-opus-20240229
    system_prompt: nami
```

## Adding Your Own AI Provider

Creating a new provider is simple:

1. Create a new file in `lib/ai_providers/your_provider.py`:

```python
from lib.ai_providers import AIProvider, Message, ChatResponse

class YourProvider(AIProvider):
    def __init__(self, config):
        super().__init__(config)
        # Initialize your AI client here

    async def chat(self, messages, tools=None, **kwargs):
        # Implement chat completion
        # Return ChatResponse object
        pass

    async def chat_stream(self, messages, tools=None, **kwargs):
        # Implement streaming
        # Yield chunks of text
        pass

    def list_models(self):
        # Return list of available models
        return ["model1", "model2"]

    def get_provider_name(self):
        return "your_provider"
```

2. Register your provider in `lib/ai_providers/__init__.py`:

```python
from .your_provider import YourProvider

ProviderRegistry.register_provider("your_provider", YourProvider)
```

3. Configure in `config.yml`:

```yaml
ai_provider: your_provider

providers:
  your_provider:
    api_key: your-key
    model: your-model
    system_prompt: nami
```

That's it! The personality proxy will now use your provider.

## API Endpoints

The API is Ollama-compatible with extensions:

### Chat Completion

**POST** `/api/chat`

```json
{
  "model": "llama2",
  "messages": [
    {"role": "user", "content": "Hello!"}
  ],
  "stream": false,
  "user_id": "alice",           // Extension: for memory
  "conversation_id": "chat123",  // Extension: for context
  "enable_memory": true,         // Extension: use memory system
  "enable_personality": true     // Extension: use personality prompt
}
```

### Generate Completion

**POST** `/api/generate`

```json
{
  "model": "llama2",
  "prompt": "Why is the sky blue?",
  "stream": false,
  "user_id": "alice",
  "enable_memory": true
}
```

### List Models

**GET** `/api/tags`

Returns available models from the current provider.

### Version Info

**GET** `/` or **GET** `/api/version`

Returns API version and capabilities.

### Health Check

**GET** `/health`

Returns provider status and memory database stats.

## Personality System

Personalities are defined in Markdown files in the `system_prompt/` directory.

**Example:** `system_prompt/nami.md`

```markdown
# Nami - AI Assistant

## Personality
- Friendly and helpful
- Technical but accessible
- Patient with explanations

## Communication Style
- Use clear, concise language
- Provide examples when helpful
- Ask clarifying questions when needed

## Behavioral Guidelines
- Always verify information before sharing
- Admit when unsure about something
- Respect user privacy and boundaries
```

Switch personalities by changing `system_prompt` in the provider config:

```yaml
providers:
  ollama:
    system_prompt: nami  # Uses system_prompt/nami.md
```

## Memory System

The Neo4j-based memory system automatically stores and retrieves relevant information.

### Memory Types

1. **Episodic Memory** - Experiences and events with emotional context
2. **Knowledge Units** - Factual information and statements
3. **Procedural Units** - Skills, processes, and how-to information

### How It Works

1. User sends a message
2. System searches Neo4j for relevant memories
3. Top memories are added to context automatically
4. AI generates response with full context
5. Important information is extracted and stored as new memories

### Memory Configuration

```yaml
memory_db:
  model: all-MiniLM-L6-v2  # Embedding model for similarity search

neo4j:
  uri: bolt://localhost:7687
  user: neo4j
  pass: your_password
```

## Tools System

The personality can use various tools automatically:

- **search_memory** - Query the memory database
- **search_web** - Search the web for information (requires Brave API key)
- **visit_web_page** - Extract content from URLs
- **generate_comfy_image** - Generate images (requires ComfyUI)
- **query_audit_log** - Access system logs

Tools are automatically loaded from `OllamaTools/` and presented to the AI.

## Project Structure

```
nami_ai/
├── api_server_ollama.py       # Main API server (Ollama-compatible)
├── lib/
│   ├── ai_providers/          # AI provider implementations
│   │   ├── base_provider.py   # Abstract base class
│   │   ├── ollama_provider.py # Ollama implementation
│   │   ├── openai_provider.py # OpenAI implementation
│   │   └── __init__.py        # Provider registry
│   ├── memory_db.py           # Neo4j memory interface
│   ├── sqlite_helper.py       # SQLite conversation history
│   └── ...
├── system_prompt/             # Personality definitions
│   ├── nami.md
│   └── ranni.md
├── OllamaTools/              # Tool implementations
├── config.yml                # Configuration
└── requirements.txt          # Dependencies
```

## Configuration Reference

### Complete Example

```yaml
# API Server
api:
  host: "0.0.0.0"
  port: 11434

# Provider Selection
ai_provider: ollama  # ollama, openai, anthropic

# Provider Configurations
providers:
  ollama:
    url: http://localhost:11434
    model: llama2
    system_prompt: nami
    max_tool_calls: 3

  openai:
    api_key: sk-your-key
    model: gpt-4
    system_prompt: nami
    organization: org-id  # optional

# Memory Database
neo4j:
  uri: bolt://localhost:7687
  user: neo4j
  pass: password

memory_db:
  model: all-MiniLM-L6-v2

# General Settings
bot:
  log_level: INFO
  brave_search_token: YOUR_TOKEN  # For web search tool

# Image Generation (optional)
comfyui:
  server: localhost:8188
  workflow: workflow.json
  output: image_output

# Image Storage (optional)
nextcloud:
  url: https://your-nextcloud.com
  user: admin
  pass: password
```

## Integration Examples

### Python with ollama Library

```python
from ollama import Client

client = Client(host='http://localhost:11434')

# Simple chat
response = client.chat(
    model='llama2',
    messages=[
        {'role': 'user', 'content': 'Hello!'}
    ]
)

# With personality extensions
response = client.chat(
    model='llama2',
    messages=[
        {'role': 'user', 'content': 'What did we talk about yesterday?'}
    ],
    options={
        'user_id': 'alice',
        'conversation_id': 'daily_chat',
        'enable_memory': True,
        'enable_personality': True
    }
)
```

### Streaming Response

```python
stream = client.chat(
    model='llama2',
    messages=[{'role': 'user', 'content': 'Tell me a story'}],
    stream=True
)

for chunk in stream:
    print(chunk['message']['content'], end='', flush=True)
```

### Using Different Personalities

```python
# Use Nami personality
client.chat(
    model='llama2',  # Note: model is from provider config
    messages=[{'role': 'user', 'content': 'Hi'}],
    options={'enable_personality': True}
)

# Change personality by updating config.yml:
# providers.ollama.system_prompt: ranni
# Then restart the server
```

## Troubleshooting

### Provider Connection Issues

**Ollama not connecting:**
```bash
# Check Ollama is running
ollama list

# Check URL in config
curl http://localhost:11434/api/tags
```

**OpenAI authentication failed:**
- Verify API key is correct
- Check organization ID (if using)
- Ensure billing is set up

### Memory Issues

**Neo4j connection failed:**
```bash
# Check Neo4j is running
systemctl status neo4j

# Test connection
cypher-shell -a bolt://localhost:7687 -u neo4j -p password
```

**No memories retrieved:**
- Check Neo4j has data
- Verify embedding model is downloaded
- Lower `similarity_threshold` in code if needed

### API Issues

**Port already in use:**
```yaml
# Change port in config.yml
api:
  port: 8080  # Use different port
```

**High latency:**
- Check AI provider response time
- Disable memory if not needed
- Reduce `max_tool_calls`

## Advanced Usage

### Custom Tool Implementation

Create a new file in `OllamaTools/my_tool.py`:

```python
async def my_custom_tool(client, source_user, param1, param2):
    """
    Custom tool implementation.
    """
    # Your logic here
    result = f"Processed {param1} and {param2}"
    return result

# Tool definition
tool_definition = {
    "type": "function",
    "function": {
        "name": "my_custom_tool",
        "description": "What this tool does",
        "parameters": {
            "type": "object",
            "properties": {
                "param1": {
                    "type": "string",
                    "description": "First parameter"
                },
                "param2": {
                    "type": "string",
                    "description": "Second parameter"
                }
            },
            "required": ["param1"]
        }
    },
    "func": my_custom_tool
}
```

Tools are automatically loaded and available to the AI.

### Running Multiple Instances

You can run multiple instances with different configurations:

```bash
# Instance 1: Ollama with Nami personality
python api_server_ollama.py --config config_nami.yml --port 11434

# Instance 2: OpenAI with Ranni personality
python api_server_ollama.py --config config_ranni.yml --port 11435
```

## License

MIT License - see LICENSE file for details.

## Contributing

Contributions welcome! To add a new AI provider:

1. Implement the `AIProvider` interface
2. Register in `ProviderRegistry`
3. Add example config to `config.yml.example`
4. Submit a pull request

## Support

- GitHub Issues: https://github.com/oOHiyoriOo/nami_ai/issues
- Documentation: This README
- Examples: See `examples/` directory (coming soon)
