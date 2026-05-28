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

## Testing

```bash
# Run all tests (test_*.py files in tests/)
python run_tests.py

# Run a single test file
python -m pytest tests/test_context_builder.py -v

# Run a single test function
python -m pytest tests/test_context_builder.py::test_platform_prefix_extracted -v
```

Tests use **pytest** and aggressively mock heavy optional dependencies (neo4j, discord, sentence_transformers, torch, asyncssh, PIL). Each test file starts with `sys.path.insert(0, str(Path(__file__).parent.parent))` so imports resolve from the project root. `run_tests.py` wraps pytest with `-v --tb=short -q` and a 60s timeout per file.

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

**Heartbeat System** (`lib/services/heartbeat_service.py`):
- Autonomous tick loop running on a configurable interval (default: 30s)
- Pluggable modules implementing `HeartbeatModule` interface with `condition()` / `action()`
- Modules each have their own cooldown and can trigger AI pipeline calls
- Built-in modules: `system_health`, `memory_grooming`, `dream` gate, `curiosity`
- Watchdog warns if no activity for `watchdog_threshold` seconds

**Dream Module** (`lib/services/dream_service.py`):
- Background memory consolidation triggered after `min_idle_hours` of silence
- Uses its own provider/model for analysis (falls back to memory extraction config)
- Deduplicates, fixes contradictions, prunes stale memories, promotes important ones
- Gated by the heartbeat module (checks every 60s, only activates during night hours by default)

**WebSocket Adapter Bridge** (`lib/services/adapter_ws_server.py`):
- External adapters (Discord, WhatsApp) connect as persistent WS clients at `/api/ws/adapter`
- Bidirectional event protocol: `capabilities.register`, `message.received`, `response.ready`, etc.
- Config is pushed to adapters via `capabilities.ack` — adapters don't need their own config files
- Adapters themselves live in `adapters/<platform>_bridge/` (separate processes, separate containers)

**Sandbox** (`lib/services/sandbox_manager.py`):
- Isolated Docker container for AI bash execution via SSH (asyncssh)
- Foreground commands auto-background after `fg_timeout` seconds
- Completed job output is surfaced in context on the next turn via `_add_completed_jobs()`
- Job management tools: `get_job_output`, `list_jobs`, `kill_job`, `reset_sandbox`

**EventBus** (`lib/services/event_bus.py`):
- Decoupled pub/sub for internal events (tool call started/completed, message received, etc.)
- Used by heartbeat watchdog, tool response logging, and notification pipeline

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

**Tool Safety Classification**: Each tool schema has a `"safe": True` or `"safe": False` field. Safe tools (read-only, no side effects) within the same round execute **concurrently** via `asyncio.gather()`. Unsafe tools (writes, shell, scheduler) run **sequentially** to avoid interleaved side effects. The tool loop also detects stuck loops: if the same tool+args fingerprint repeats `max_calls` times consecutively, the model is re-prompted once without tools.

**Tool Return Format**: Tools must return JSON strings via `tool_success(data, **extra)` or `tool_error(message, **extra)` from `OllamaTools/__init__.py`. The response is parsed by `tool_executor.py` — `success=True/False` determines whether the result is fed back to the model.

**Pipeline Context** (`ContextVar`): `lib/services/ai_pipeline.py` defines `pipeline_ctx: ContextVar[dict]` which is set before tool execution. Tools like `schedule_task` read `pipeline_ctx.get()` to get the caller's `user_id` / `conversation_id` without explicit parameter injection. ContextVar is asyncio-safe — each task gets its own copy.

**Speaker Attribution**: `ai_pipeline_handler.py` prefixes stored user messages as `[DisplayName]: ...` before they reach the model, and strips a mirrored `[Nami]:` prefix from model output. This lets the LLM distinguish who said what in multi-user channels.

**Thinking Mode**: Trigger words in `config.yml` → `thinking.trigger_words` (e.g., "think deeply") auto-enable a heavier model for the current turn. `think_override` can be set explicitly via the API. Vision preprocessing runs before model selection so images are available regardless of which model is used.

**Vision Fallback**: When the chat model lacks vision capabilities, `vision_service.py` calls a dedicated vision model (e.g., `llama3.2-vision:11b`) to describe images, then injects the description as text into the chat context.

**Provider Registry**: Providers are registered and instantiated on-demand. The model cache (`lib/services/model_cache.py`) tracks successfully validated models.

**ToolContext** (`lib/services/tool_context.py`): Bundles tools, provider-safe schemas (stripped of `"func"` and `"safe"` fields), and a name→callable map. Two factories: `for_chat()` (full tool set) and `for_heartbeat()` (filtered by module-declared categories like `memory_read`, `memory_write`).

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