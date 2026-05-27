# Nami AI — Personal AI Assistant (Jarvis-style)

Nami is a **Jarvis-style personal AI assistant**: one persistent AI personality that talks to real named people, remembers people and places, and carries the same identity across direct API usage and chat platforms. She still exposes an **Ollama-compatible API**, but the product goal is a personal assistant with memory and continuity — **not** a generic multi-user LLM proxy.

## ✨ Features

- 🔌 **Ollama-Compatible API** - Use Nami with any Ollama client or call the REST API directly
- 🧠 **Multiple AI Backends** - Route requests to Ollama, OpenAI, GitHub Copilot, and other providers with `<provider>/<model>`
- 🪪 **Identity-Aware Context** - Injects a `role=tool`, `name=user_info` JSON message right after the system prompt so Nami always knows who she is talking to
- 🗣️ **Speaker Attribution** - Multi-user history is prefixed as `[DisplayName]: ...` so the LLM can track who said what
- 💾 **Neo4j Memory Graph** - Long-term episodic, knowledge, and procedural memory with Person, Location, and Concept nodes
- 🌍 **Location Memory** - Memories can link to `(:Location)` nodes with `OCCURRED_AT` relationships extracted from conversation
- 🔗 **Cross-Platform Identity** - `SAME_PERSON_AS` links let Nami share memory across scoped IDs like `discord:123` and `whatsapp:+15551234567`
- 🛠️ **Graph + Sandbox Tools** - Memory search, web fetch, sandbox execution, and person/location graph tools built in
- 🔌 **MCP Support** - Load remote Model Context Protocol tools alongside local tools
- 💬 **Shared Identity Across Surfaces** - REST API, Discord adapter, and outbound WhatsApp messaging all talk to the same Nami
- 📝 **Conversation History** - SQLite-backed per-conversation history with named speakers
- 🔒 **Privacy-Focused** - Runs locally; your memory graph and chat history stay under your control

## 🚀 Quick Start

```bash
# 1. Clone and install
git clone https://github.com/oOHiyoriOo/nami_ai.git
cd nami_ai
pip install -r requirements.txt

# 2. Configure
cp config.yml.example config.yml
nano config.yml

# 3. Run
python api_server.py
```

**Use with any Ollama client:**

```bash
export OLLAMA_HOST=http://localhost:11434
curl http://localhost:11434/api/chat -d '{
  "model": "ollama/llama3.2",
  "messages": [{"role": "user", "content": "Hello, Nami"}],
  "user_id": "discord:123456789",
  "conversation_id": "discord-dm-123456789"
}'
```

```python
from ollama import Client

client = Client(host='http://localhost:11434')
response = client.chat(
    model='ollama/llama3.2',
    messages=[{'role': 'user', 'content': 'Remember that I prefer dark mode.'}],
    options={'user_id': 'discord:123456789', 'enable_memory': True}
)
```

[**→ Full Quick Start Guide**](docs/guides/quickstart.md)

### Docker Compose

```bash
cp .env.example .env
docker compose up -d nami_neo4j nami_ai

# Optional profiles
# docker compose --profile sandbox up -d nami_ai sandbox
# docker compose --profile discord up -d nami_ai discord_bridge
# docker compose --profile whatsapp up -d nami_ai whatsapp_bridge
```

With the bundled Compose stack, the API is exposed on `http://127.0.0.1:11435`, Neo4j Browser on `http://127.0.0.1:7475`, and Bolt on `127.0.0.1:7688`. Because apparently one port number wasn't enough drama.

## 📚 Documentation

| Guide | Description |
|-------|-------------|
| [**Quick Start**](docs/guides/quickstart.md) | Get up and running quickly |
| [**Architecture**](docs/ARCHITECTURE.md) | System layout, data flow, and graph model |
| [**AI Providers**](docs/reference/providers.md) | Switch between Ollama, OpenAI, Copilot, or add your own |
| [**API Reference**](docs/reference/api.md) | Ollama-compatible REST API reference |
| [**Memory System**](docs/memory/overview.md) | Neo4j memory graph, people, places, and identity linking |
| [**Memory V2 Features**](docs/memory/advanced-features.md) | Hierarchy, decay, consolidation, analytics |
| [**Tools**](docs/reference/tools.md) | Built-in tools and custom tool authoring |

## 🏗️ Architecture

```text
Client / Adapter → Nami AI Personal Assistant
                      ├── API Layer (Ollama-compatible)
                      ├── AI Providers (Ollama / OpenAI / Copilot / ...)
                      ├── Context Builder
                      │   ├── System Prompt
                      │   ├── user_info tool message
                      │   └── Relevant memories
                      ├── Tool Loop
                      ├── Neo4j Memory Graph
                      ├── SQLite Conversation History
                      └── Chat Adapters (Discord, WhatsApp outbound)
```

Nami is built around a single identity: the same assistant personality is available through direct API calls and platform adapters, with shared memory across surfaces.

## 🔌 Multi-Provider Support

Use the `<provider>/<model>` format to switch backends without changing the API surface:

```yaml
providers:
  ollama:
    url: http://localhost:11434

  copilot:
    enabled: true

  openai:
    api_key: sk-your-key
```

```bash
curl -X POST http://localhost:11434/api/chat -d '{"model": "ollama/llama3.2", ...}'
curl -X POST http://localhost:11434/api/chat -d '{"model": "copilot/gpt-4.1", ...}'
curl -X POST http://localhost:11434/api/chat -d '{"model": "openai/gpt-4", ...}'
```

Successful model/provider combinations are cached and returned by `GET /api/tags`.

## 🎭 Personalities

Personalities are Markdown files in `system_prompt/`:

```markdown
# Nami - AI Assistant

## Personality
- Friendly and observant
- Remembers people and context

## Communication Style
- Use clear, concise language
- Stay consistent across platforms
```

Switch personalities in `config.yml`:

```yaml
default_system_prompt: nami
```

## 🪪 Identity-Aware Context

For normal chat requests, Nami's prompt starts with:

1. the personality system prompt
2. a `role=tool`, `name=user_info` JSON payload
3. any injected memory context
4. the conversation history

The `user_info` payload includes:

```json
{
  "user": "<username>",
  "username": "<username>",
  "user_id": "discord:123456789",
  "platform": "Discord",
  "channel": "lab-chat",
  "guild": "Example Lab",
  "is_dm": false
}
```

That gives the model explicit identity and place context instead of hoping it infers everything from raw chat text. Wild idea, I know.

## 💾 Memory System

Nami stores long-term memory in Neo4j as a graph:

- `(:Person)` nodes for real people and identities
- `(:Location)` nodes for places mentioned in memories
- `(:EpisodicMemory)`, `(:KnowledgeUnit)`, `(:ProceduralUnit)` nodes for remembered content
- `(:CONCEPT)` nodes for extracted concepts

Key relationships:

- `(Person)-[:IS_AUTHOR_OF]->(Memory)`
- `(Memory)-[:OCCURRED_AT]->(Location)`
- `(Memory)-[:REFERS_TO_CONCEPT]->(CONCEPT)`
- `(Memory)-[:IS_ABOUT]->(Person)`
- `(Person)-[:SAME_PERSON_AS]-(Person)`

`context_builder.py` resolves linked identities before retrieval, and `memory_service.get_formatted_memories_multi_user()` deduplicates memory hits by memory ID so Nami can pull one coherent memory block across platforms.

[**→ Memory System Guide**](docs/memory/overview.md)

## 🛠️ Tools

Normal chat sessions expose these core built-in tools:

| Tool | Purpose |
|------|---------|
| `search_memory(query, person="")` | Search memories globally or scoped to a specific person |
| `search_web(query, num_results=5)` | Find candidate web pages before opening them with browser tools |
| `mcp_playwright_browser_navigate` + `mcp_playwright_browser_snapshot` | Browse pages with real Chromium (JS rendering, anti-bot) |
| `run_bash(command)` / `sandbox_read_file(path)` / `sandbox_write_file(path, content)` / `sandbox_list_dir(path)` | Work inside the optional sandbox container |
| `get_job_output(job_id)` / `list_jobs()` / `kill_job(job_id)` / `reset_sandbox()` | Manage sandbox jobs |
| `create_person(name, description="", relationship="")` | Upsert a `Person` node |
| `create_location(name, description="")` | Upsert a `Location` node |
| `remember_about_person(person_name, fact)` | Store a `KnowledgeUnit` linked to a person via `IS_ABOUT` |
| `link_my_identity(other_platform, other_id)` | Link the current user to another platform identity |
| `send_message(...)` / `schedule_task(...)` / `queue_research(...)` | Send proactive messages and schedule autonomous work |

MCP tools are also supported and are exposed with the `mcp_<server>_` prefix.

[**→ Tools Guide**](docs/reference/tools.md)

## 📡 API Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /api/chat` | Chat completion (Ollama format) |
| `POST /api/generate` | Text generation |
| `GET /api/tags` | List cached validated models |
| `GET /api/models/stats` | Model cache statistics |
| `GET /health` | Health check |

**Model format:** `<provider>/<model>` (for example `ollama/llama3.2`, `copilot/gpt-4.1`)

[**→ API Reference**](docs/reference/api.md)

## 🎯 Use Cases

- **Personal AI Assistant** - One assistant identity that knows you across surfaces
- **Cross-Platform Companion** - Continue the same relationship in API calls, Discord, and future adapters
- **People & Place Memory** - Remember who someone is, where something happened, and how those facts connect
- **Home Lab / Project Assistant** - Keep persistent technical context and use sandbox tools when enabled
- **Research Companion** - Combine memory, web retrieval, and tools without losing continuity

## 🤖 Clients & Integrations

- **Direct API Use** - Call Nami through the Ollama-compatible REST API
- **Discord Bridge** - External WebSocket bridge in `adapters/discord_bridge/` with shared identity and history sync
- **WhatsApp Bridge** - External WebSocket bridge in `adapters/whatsapp_bridge/` for outbound delivery flows
- **Ollama-Compatible Clients** - Ollama CLI, Open WebUI, LangChain, and custom clients can all target Nami's API

## 🤝 Contributing

Contributions are welcome. To add a new AI provider:

1. Implement the provider interface in `lib/ai_providers/`
2. Register it in the provider registry
3. Add config to `config.yml.example`
4. Open a pull request on GitHub

[**→ Provider Development Guide**](docs/reference/providers.md#creating-a-custom-provider)

## 🗂️ Project Structure

```text
nami_ai/
├── api_server.py
├── run_tests.py
├── docker-compose.yml
├── .env.example
├── config/
│   └── playwright-mcp.json
├── scripts/
│   ├── nami_start.sh
│   ├── sandbox_start.sh
│   └── sandbox_jetson_start.sh
├── adapters/
│   ├── discord_bridge/
│   └── whatsapp_bridge/
├── lib/
│   ├── ai_providers/
│   ├── chat_adapters/
│   ├── services/
│   ├── mcp_client.py
│   ├── tool_loader.py
│   └── memory_db.py
├── mcp/
│   ├── gitea-mcp-stdio/
│   └── neo-memory-mcp/
├── OllamaTools/
│   ├── search_memory.py
│   ├── search_web.py
│   ├── sandbox_read_file.py
│   ├── sandbox_write_file.py
│   ├── sandbox_list_dir.py
│   ├── run_bash.py
│   ├── schedule_task.py
│   ├── send_message.py
│   └── ...
├── system_prompt/
├── tests/
├── docs/
├── config.yml.example
└── requirements.txt
```

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

## 🆘 Support

- **Documentation**: [docs/](docs/)
- **Repository**: https://github.com/oOHiyoriOo/nami_ai
- **Issues**: https://github.com/oOHiyoriOo/nami_ai/issues

---

**Made for building a memory-rich personal AI assistant instead of yet another forgetful chat box.**
