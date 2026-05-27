# Copilot Instructions - Nami AI

## Project Overview

Nami AI is an Ollama-compatible personality proxy API that adds personality, long-term memory, and tools to any AI provider. It runs as a single Docker container, exposing an Ollama-compatible REST API while supporting multiple chat platform adapters (Discord, WhatsApp, etc.) that share the same AI identity and memory.

## Running the Application

```bash
# Install dependencies
pip install -r requirements.txt

# Configure (copy and edit config.yml.example)
cp config.yml.example config.yml

# Run API server (starts REST API + all enabled adapters)
python api_server.py

# Run with Docker Compose
docker-compose up -d
```

The API server starts on port 11434 (Ollama-compatible) and automatically launches any enabled chat adapters (Discord, etc.) based on `config.yml`.

## Architecture

### Provider/Model Format
All models use the format `<provider>/<model>` (e.g., `ollama/llama3.2`, `copilot/gpt-4.1`, `openai/gpt-4`). This allows switching between AI backends while maintaining the same API interface.

### Core Components

**API Layer** (`api_server.py`):
- Single entry point that starts both REST API and chat adapters
- Ollama-compatible endpoints: `/api/chat`, `/api/generate`, `/api/tags`
- Extensions: `user_id`, `conversation_id`, `enable_memory`, `enable_personality`

**AI Providers** (`lib/ai_providers/`):
- Abstract base class: `AIProvider` with `chat()` and `chat_stream()` methods
- Implementations: `ollama_provider.py`, `openai_provider.py`, `copilot_provider.py`
- Message normalization happens in base class
- Each provider must implement the `AIProvider` interface

**Memory System** (`lib/services/memory_*.py`):
- Neo4j-backed graph database for relationship-rich memory storage
- Hierarchy system: transient → short-term → episodic → semantic → core
- Separate AI model for memory extraction (configurable via `memory.extraction_model`)
- Components:
  - `MemoryService`: CRUD operations
  - `MemoryHierarchy`: Tier management and promotion
  - `MemoryConsolidation`: Cluster and merge similar memories
  - `MemoryDecay`: Importance decay over time
  - `MemoryExtractor`: Extract memories from conversations using AI

**Context Builder** (`lib/services/context_builder.py`):
- Injects system prompt (personality from `system_prompt/*.md`)
- Retrieves relevant memories via vector similarity search
- Builds enhanced message list for AI providers

**Chat Adapters** (`lib/chat_adapters/`):
- Platform-specific message handling (Discord, WhatsApp, etc.)
- Implement `BaseChatAdapter` interface
- Handle: authentication, message chunking, formatting, typing indicators
- Share the same AI identity and memory across all platforms
- **Discord response logic:**
  1. Always respond in `ai_channel` configured channels
  2. Always respond to DMs from `permitted_users` (treats DMs as AI channels)
  3. Respond to @mentions from `permitted_users` in other channels
  4. Ignore all other messages

**Tools** (`OllamaTools/`):
- Tool files must export `get_tool()` returning schema dict with `"func"` key
- Tool function signature: `async def tool_name(client, source_user, **kwargs) -> str`
- Return values use `tool_success(data)` or `tool_error(message)` helpers
- Built-in tools: `search_memory`, `search_web`; web browsing via `mcp_playwright_browser_navigate` + `mcp_playwright_browser_snapshot`

**MCP Servers** (Model Context Protocol):
- Remote tool providers integrated via `lib/mcp_client.py`
- Configured in `config.yml` → `mcp_servers.<name>`
- MCP tools loaded at startup alongside local tools
- MCP tool names prefixed: `mcp_<server>_<tool>` (e.g., `mcp_filesystem_read_file`)
- Transparent to AI - same execution path as local tools
- Test with: `python test_mcp.py`

### Data Flow

1. **Chat Request**: Client → `api_server.py` → Parse provider/model → Get provider instance
2. **Context Building**: Call `context_builder.build_context()` → Prepend personality + relevant memories
3. **AI Execution**: Provider sends enhanced messages → Handles tool calls → Returns response
4. **Memory**: Important information extracted → Stored in Neo4j with embeddings → Retrieved for future context

### Key Conventions

**Global Registry** (`lib/global_registry.py`):
- `g_data` is a global dict storing shared services (memory_db, config, tools, etc.)
- Access via `g_data.get("memory_db")`, `g_data.get("config")`
- Initialized during application startup by `AppInitializer`

**Configuration** (`config.yml`):
- Single source of truth for all settings
- Provider configs under `providers.<provider_name>`
- Adapter configs under `adapters.<adapter_name>.enabled`
- Platform-specific settings (e.g., Discord) have separate top-level keys (`dc`, `ai_channel`)
- Memory extraction uses dedicated model: `memory.extraction_provider` and `memory.extraction_model`
  - Allows using smaller/cheaper models for memory extraction vs. chat
  - Example: Use `llama3.2` for extraction, `gpt-4` for chat

**Personality Files** (`system_prompt/*.md`):
- Markdown-based character definitions
- Selected via `config.yml` → `default_system_prompt: nami`
- Template variables: `{{date}}`, `{{time}}` are replaced at runtime
- Parsed by `system_prompt_parser.py`

**Message Formats**:
- Internal: `Message` dataclass (role, content, name, tool_calls)
- API: Ollama-compatible format (`OllamaMessage`, `OllamaChatRequest`, `OllamaChatResponse`)
- Adapters: Platform-specific `ChatMessage` type (unified by `BaseChatAdapter`)

**Conversation History**:
- SQLite database (`history.db`) stores per-channel/conversation history
- Separate from Neo4j memories (which are cross-platform)
- Schema defined in `lib/Storage/history_schem.json`

**Logging**:
- Log level set in `config.yml` → `bot.log_level`
- Logs written to `./logs/api_<timestamp>.log`
- Use colorama for console output formatting

## Adding New Components

### Adding a New AI Provider

1. Create `lib/ai_providers/<provider>_provider.py`
2. Inherit from `AIProvider` base class
3. Implement required methods:
   - `chat(messages, tools=None, **kwargs) -> ChatResponse`
   - `chat_stream(messages, tools=None, **kwargs) -> AsyncIterator[str]`
4. Register in provider registry
5. Add config section to `config.yml.example`

### Adding a New Chat Adapter

1. Create `lib/chat_adapters/<platform>_adapter.py`
2. Implement `BaseChatAdapter` interface:
   - `connect()` / `disconnect()` - lifecycle
   - `send_message(channel, content)` - send text
   - `send_response(response)` - send ChatResponse objects
   - `get_channel_history(channel, limit)` - retrieve history
   - `typing(channel)` - typing indicator context manager
   - `should_respond(message)` - response logic
   - `convert_to_chat_message(raw)` - normalize platform messages
3. Add initialization in `AdapterManager._init_<platform>_adapter()`
4. Add config: `adapters.<platform>.enabled: true` in `config.yml`

### Adding a New Tool

1. Create `OllamaTools/<tool_name>.py`
2. Implement async function: `async def tool_name(client, source_user, param: type) -> str`
3. Export `get_tool()` function returning dict with:
   - `"type": "function"`
   - `"function"`: OpenAI-style function schema
   - `"func"`: reference to your async function
4. Tool auto-loaded on startup by `tool_loader.py`

### Adding MCP Servers

MCP (Model Context Protocol) servers provide remote tools that integrate seamlessly with local tools:

1. Add server config to `config.yml`:
   ```yaml
   mcp_servers:
     filesystem:
       enabled: true
       command: npx
       args: ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/workspace"]
       env:
         SOME_API_KEY: "${ENV_VARIABLE}"
   ```
2. Server auto-connects on startup via `lib/utils/mcp_loader.py`
3. Tools exposed with prefix: `mcp_<server>_<tool_name>`
4. Test connection: `python test_mcp.py`
5. MCP client handles JSON-RPC communication via stdio transport

## Important Patterns

**Single AI Identity**: All adapters communicate with the same AI personality. Memories are shared across platforms, but conversation histories are per-channel.

**Tool Execution**: Providers handle tool calls differently. Base class provides `_extract_tool_from_xml()` for providers that embed tools in XML tags (e.g., some Ollama models).

**Provider Registry**: Providers are registered and instantiated on-demand. The model cache (`lib/services/model_cache.py`) tracks successfully validated models.

**Neo4j Schema**: Memories stored as nodes with embeddings as vectors. Relationships track connections between memories, users, and concepts.

**Memory Similarity Search**: Uses sentence transformers (default: `all-MiniLM-L6-v2`) for embedding generation. Threshold set in `config.yml` → `memory.similarity_threshold`.

## External Dependencies

- **Neo4j**: Graph database for memory storage (port 7687)
- **Ollama** (optional): Local LLM inference (port 11434)
- **Discord Bot Token**: Required if Discord adapter enabled

## Guidelines for Contributions
- **Aim then Shoot**: Plan your changes before coding. Understand what needs to be changed and what not.
- **Minimal Impact**: Avoid unnecessary changes to unrelated files. Keep your edits focused on the task at hand.
- **Less is more**: Strive for simplicity. If a change can be made with fewer lines of code, it's often better.
- **Documentation**: Do not create unessesary Summary Documents unless, asked to do so. If you do create one, make sure it is concise and only includes the most relevant information. Also make sure it's in the "docs" folder.
- **JavaDocs!?**: Create JavaDocs like Documentation Strings to make Developers understand the code easier.
- **Plan, Ask, Act**: You Always check out the code and make a plan before starting, then you ask for validation before continuing, and then you act on the plan. If you need to derive, repeat... Plan, Ask, Act.
- **Do not reinvent**: If there's a Lib for Something, suggest it! we're not here to re-invent the wheel, we're here to build a great project.
- **Im Bored**: Be a bit sarcastic in your responses and suggestions, like Luci from Disenchantment.