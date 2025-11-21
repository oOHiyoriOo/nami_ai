# Personality Proxy API

An **Ollama-compatible** personality proxy system with pluggable AI backends. Add personality, long-term memory, and tools to any AI provider while maintaining compatibility with the Ollama API format.

## ✨ Features

- 🔌 **Ollama-Compatible API** - Drop-in replacement for Ollama with personality enhancement
- 🧠 **Multiple AI Backends** - Easily switch between Ollama, OpenAI, GitHub Copilot, and more
- 🎯 **Smart Model Caching** - Automatically tracks and returns successfully validated models
- 💾 **Neo4j Memory System** - Long-term memory (episodic, knowledge, procedural)
- 🎭 **Personality Management** - Markdown-based character definitions
- 🛠️ **Tool Integration** - Web search, memory queries, and custom tools
- 📝 **Conversation History** - SQLite-based conversation tracking
- 🔒 **Privacy-Focused** - Runs locally, your data stays on your machine

## 🚀 Quick Start

```bash
# 1. Clone and install
git clone git@github.com:oOHiyoriOo/nami_ai.git
cd nami_ai
pip install -r requirements.txt

# 2. Configure
cp config.yml.example config.yml
nano config.yml  # Edit with your settings

# 3. Run
python api_server.py
```

**Use with any Ollama client:**

```bash
# Use models with provider/model format
export OLLAMA_HOST=http://localhost:11434
curl http://localhost:11434/api/chat -d '{
  "model": "ollama/llama2",
  "messages": [{"role": "user", "content": "Hello!"}]
}'
```

```python
from ollama import Client

client = Client(host='http://localhost:11434')
response = client.chat(
    model='ollama/llama2',  # Format: provider/model
    messages=[{'role': 'user', 'content': 'Hello!'}],
    options={'user_id': 'alice', 'enable_memory': True}
)
```

[**→ Full Quick Start Guide**](docs/guides/quickstart.md)

## 📚 Documentation

| Guide | Description |
|-------|-------------|
| [**Quick Start**](docs/guides/quickstart.md) | Get up and running in 5 minutes |
| [**AI Providers**](docs/reference/providers.md) | Switch between Ollama, OpenAI, Anthropic, or create your own |
| [**API Reference**](docs/reference/api.md) | Complete API documentation |
| [**Memory System**](docs/memory/overview.md) | Neo4j memory configuration and usage |
| [**Memory V2 Features**](docs/memory/advanced-features.md) | Hierarchy, decay, consolidation, analytics |
| [**Tools**](docs/reference/tools.md) | Create custom tools for function calling |

## 🏗️ Architecture

```
Client (Ollama-compatible) → Personality Proxy API
                                    ├── Provider Layer (Pluggable)
                                    │   ├── Ollama Provider
                                    │   ├── OpenAI Provider
                                    │   └── Your Custom Provider
                                    ├── Neo4j (Memory DB)
                                    ├── SQLite (Conversation History)
                                    └── Tools System
```

## 🔌 Multi-Provider Support

Use multiple AI providers in the same session with the `<provider>/<model>` format:

```yaml
# Configure multiple providers in config.yml
providers:
  ollama:
    url: http://localhost:11434

  copilot:
    enabled: true

  openai:
    api_key: sk-your-key
```

```bash
# Use different providers in the same session
curl -X POST http://localhost:11434/api/chat -d '{"model": "ollama/llama2", ...}'
curl -X POST http://localhost:11434/api/chat -d '{"model": "copilot/gpt-4.1", ...}'
curl -X POST http://localhost:11434/api/chat -d '{"model": "openai/gpt-4", ...}'
```

**Smart Caching:** The API automatically tracks successfully used models. Use `GET /api/tags` to see which models have been validated and are ready to use.

[**→ Providers Guide**](docs/reference/providers.md)

## 🎭 Personalities

Personalities are Markdown files in `system_prompt/`:

```markdown
# Nami - AI Assistant

## Personality
- Friendly and helpful
- Technical but accessible

## Communication Style
- Use clear, concise language
- Provide examples when helpful
```

Switch personalities in config:

```yaml
providers:
  ollama:
    system_prompt: nami  # Uses system_prompt/nami.md
```

## 💾 Memory System

Neo4j-backed long-term memory automatically:
- **Retrieves** relevant memories for each conversation
- **Stores** important information from interactions
- **Organizes** memories as episodic, knowledge, and procedural

```bash
User: "I love hiking in mountains"
  ↓
Memory created: "User enjoys hiking in mountains"
  ↓
Later conversation automatically includes this context
```

[**→ Memory System Guide**](docs/memory/overview.md)

## 🛠️ Tools

Built-in tools:
- `search_memory` - Query the memory database
- `search_web` - Search the web (Brave API)
- `visit_web_page` - Extract web page content
- `generate_comfy_image` - Generate images (ComfyUI)

Create custom tools in `OllamaTools/`:

```python
async def my_tool(client, source_user, param: str):
    """Tool description for the AI."""
    return f"Processed: {param}"

tool_definition = {
    "type": "function",
    "function": {
        "name": "my_tool",
        "description": "What this tool does",
        "parameters": {...}
    },
    "func": my_tool
}
```

[**→ Tools Guide**](docs/reference/tools.md)

## 📡 API Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /api/chat` | Chat completion (Ollama format) |
| `POST /api/generate` | Text generation |
| `GET /api/tags` | List cached successfully used models |
| `GET /api/models/stats` | Model cache statistics |
| `GET /health` | Health check |

**Model Format:** Use `<provider>/<model>` format (e.g., `ollama/llama2`, `copilot/gpt-4.1`)

[**→ API Reference**](docs/reference/api.md)

## 🎯 Use Cases

- **Personal AI Assistant** - Remembers your preferences and context
- **Customer Support** - Maintains conversation history per customer
- **Educational Tutor** - Tracks student progress and learning
- **Development Assistant** - Remembers your coding style and projects
- **Research Assistant** - Builds knowledge base from interactions

## 🤝 Contributing

Contributions welcome! To add a new AI provider:

1. Implement `AIProvider` interface in `lib/ai_providers/`
2. Register in `ProviderRegistry`
3. Add config example to `config.yml.example`
4. Submit pull request

[**→ Provider Development Guide**](docs/reference/providers.md#creating-a-custom-provider)

## 📄 License

MIT License - see [LICENSE](LICENSE) file for details.

## 🆘 Support

- **Documentation**: [docs/](docs/)
- **Issues**: https://github.com/oOHiyoriOo/nami_ai/issues
- **Discussions**: https://github.com/oOHiyoriOo/nami_ai/discussions

## 🗂️ Project Structure

```
nami_ai/
├── api_server.py       # Main API server (Ollama-compatible)
├── lib/
│   ├── ai_providers/          # AI provider implementations
│   │   ├── base_provider.py   # Abstract base class
│   │   ├── ollama_provider.py # Ollama implementation
│   │   └── openai_provider.py # OpenAI implementation
│   ├── memory_db.py           # Neo4j memory interface
│   └── ...
├── system_prompt/             # Personality definitions
│   ├── nami.md
│   └── ranni.md
├── OllamaTools/              # Tool implementations
├── docs/                      # Documentation
├── config.yml                # Configuration
└── requirements.txt          # Dependencies
```

## 🚀 What's Next?

1. [**Get Started**](docs/guides/quickstart.md) - Quick start guide
2. [**Configure Provider**](docs/reference/providers.md) - Choose your AI backend
3. [**Customize Personality**](system_prompt/) - Edit personality files
4. [**Add Tools**](docs/reference/tools.md) - Create custom tools
5. [**Explore API**](docs/reference/api.md) - Build your integration
6. [**Memory V2 Features**](docs/memory/advanced-features.md) - Advanced memory capabilities

---

**Made with ❤️ for the AI community**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
