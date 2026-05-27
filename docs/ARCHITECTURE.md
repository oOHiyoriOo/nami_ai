# Nami AI - Architecture

**Version**: 2.1  
**Updated**: May 2026

---

## Overview

Nami AI is a **Jarvis-style personal assistant runtime** with persistent graph memory. It runs as a single application that exposes an Ollama-compatible REST API and can also host chat adapters such as Discord. Every surface talks to the same assistant identity.

```text
┌─────────────────────────────────────────────────────────────────────┐
│                         Nami AI Runtime                             │
├─────────────────────────────────────────────────────────────────────┤
│  Discord Adapter   WhatsApp (outbound)   Direct REST API           │
│         │                  │                    │                   │
│         └──────────────────┴────────────────────┘                   │
│                            ▼                                        │
│                      ai_pipeline.py                                 │
│          ┌─────────────────┼──────────────────┐                     │
│          ▼                 ▼                  ▼                     │
│   Context Builder     AI Provider        Tool Executor             │
│          │                 │                  │                     │
│          ▼                 ▼                  ▼                     │
│     Memory Service   Ollama/OpenAI/     Local + MCP Tools          │
│          │           Copilot/...                                 │
│          ▼                                                      │
│      Neo4j Memory Graph + SQLite Conversation History              │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Core Principles

### 1. Single AI Identity
All adapters and direct API calls talk to the **same Nami**. Personality comes from the selected system prompt, not from the calling surface.

### 2. Knows Who She's Talking To
`context_builder.py` injects a `role=tool`, `name=user_info` JSON message immediately after the system prompt. That second prompt slot contains display name, username, scoped user ID, platform, channel, guild, and DM state.

### 3. Shared Memory Graph
Nami stores memories in Neo4j and can share them across linked platform identities. `resolve_canonical_users()` traverses `SAME_PERSON_AS`, and retrieval deduplicates results by `memory_id`.

### 4. Separate Conversation Histories with Named Speakers
Conversation history is stored per channel/conversation (for example via `discord_history.py`), but user turns are prefixed as `[DisplayName]: ...` before they reach the model so multi-user channels stay attributable.

### 5. Platform-Agnostic Core
The AI pipeline, memory layer, and tool loop live in `lib/services/` and `lib/ai_providers/`. Adapters handle platform-specific concerns such as auth, formatting, and delivery.

---

## Directory Structure

```text
nami_ai/
├── api_server.py                   # Main entry point / Ollama-compatible REST API
├── config.yml                      # Runtime configuration
├── docker-compose.yml              # Compose stack for API, Neo4j, sandbox, and bridges
├── requirements.txt                # Python dependencies
│
├── config/
│   └── playwright-mcp.json         # Playwright MCP browser settings
│
├── scripts/
│   ├── nami_start.sh               # Container startup / dependency bootstrap
│   ├── sandbox_start.sh            # Standard sandbox bootstrap
│   └── sandbox_jetson_start.sh     # Jetson sandbox bootstrap
│
├── adapters/
│   ├── discord_bridge/             # External Discord WebSocket bridge
│   └── whatsapp_bridge/            # External WhatsApp WebSocket bridge
│
├── lib/
│   ├── ai_providers/
│   │   ├── base_provider.py
│   │   ├── ollama_provider.py
│   │   ├── openai_provider.py
│   │   └── copilot_provider.py
│   │
│   ├── services/
│   │   ├── ai_pipeline.py          # Shared request pipeline
│   │   ├── ai_pipeline_handler.py  # Adapter-facing orchestration + speaker attribution
│   │   ├── adapter_manager.py      # Outbound adapter routing over WebSocket
│   │   ├── context_builder.py      # System prompt + user_info + memory injection
│   │   ├── discord_history.py      # SQLite-backed Discord history store
│   │   ├── memory_service.py       # Retrieval + formatting + multi-user dedupe
│   │   ├── memory_hierarchy.py     # Tiered memory retrieval
│   │   ├── memory_consolidation.py # Cluster / merge similar memories
│   │   ├── memory_decay.py         # Decay-aware ranking
│   │   ├── memory_extractor.py     # LLM-based fact/location extraction
│   │   ├── memory_processor.py     # Persists extracted memories into Neo4j
│   │   ├── sandbox_manager.py      # Isolated command execution
│   │   └── tool_executor.py        # Shared tool loop for API + adapters
│   │
│   ├── chat_adapters/
│   │   ├── base_adapter.py         # Shared adapter contracts / types
│   │   └── types.py
│   │
│   ├── mcp_client.py               # MCP server integration
│   ├── tool_loader.py              # Backward-compatible tool loader wrapper
│   └── memory_db.py                # Neo4j persistence + vector search
│
├── mcp/
│   ├── gitea-mcp-stdio/            # Built-in Gitea MCP server
│   └── neo-memory-mcp/             # Neo4j memory MCP server submodule
│
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
│
├── system_prompt/
├── tests/
└── docs/
```

---

## Key Components

### API Server (`api_server.py`)
- Starts the REST API and enabled chat adapters
- Exposes Ollama-compatible endpoints: `/api/chat`, `/api/generate`, `/api/tags`, `/health`
- Resolves models in `<provider>/<model>` format
- Extends requests with Nami-specific context such as `user_id`, `conversation_id`, `enable_memory`, `enable_personality`, `image_urls`, `think_override`, `options`, `display_name`, `channel_name`, `guild_name`, and `is_dm`

### AI Pipeline (`lib/services/ai_pipeline.py`)
- Platform-agnostic request runner for adapters and direct API use
- Builds enhanced context, preprocesses images, resolves thinking mode, executes tool calls, and schedules memory extraction
- `AIPipelineRequest` is the common input model shared by every surface

### AI Pipeline Handler (`lib/services/ai_pipeline_handler.py`)
- Builds platform history before passing requests into the shared pipeline
- Prefixes stored user messages as `[DisplayName]: ...` so the LLM can distinguish speakers
- Strips a mirrored `[Nami]:` prefix from model output before sending the final response

### Context Builder (`lib/services/context_builder.py`)
- Prepends the active system prompt
- Injects `user_info` as **slot 2** in the final prompt
- Resolves linked identities with `memory_db.resolve_canonical_users()`
- Calls `memory_service.get_formatted_memories_multi_user()` to fetch one deduplicated memory block across canonical IDs
- Appends completed sandbox job notifications as system context when relevant

### Memory System (`lib/memory_db.py`, `lib/services/memory_*`)
Memory is stored in Neo4j as a graph with vector search over memory nodes.

Current graph schema:

```text
(:Person {id, name, nickname})
(:Location {location_id, name, description})
(:EpisodicMemory)
(:KnowledgeUnit)
(:ProceduralUnit)
(:CONCEPT)

(Person)-[:IS_AUTHOR_OF]->(Memory)
(Memory)-[:OCCURRED_AT]->(Location)
(Memory)-[:REFERS_TO_CONCEPT]->(CONCEPT)
(Memory)-[:IS_ABOUT]->(Person)
(Person)-[:SAME_PERSON_AS]-(Person)
```

Supporting services:
- `MemoryService` formats and deduplicates retrieved memories
- `MemoryHierarchy` retrieves across transient → short-term → episodic → semantic → core
- `MemoryConsolidation` merges redundant memories
- `MemoryDecay` adjusts ranking based on age and access patterns
- `MemoryExtractor` parses facts, concepts, and locations from conversation turns
- `MemoryProcessor` stores memories, concepts, authors, and location links

### Chat Adapters (`adapters/` + `lib/chat_adapters/`)
- `adapters/discord_bridge/` and `adapters/whatsapp_bridge/` are the external bridge services that connect over WebSocket
- `lib/chat_adapters/` contains the shared Python adapter contracts and message types used by the core runtime
- This split keeps platform-specific SDK code out of the API process while preserving one shared AI identity

### Tools (`OllamaTools/` + MCP)
- Local tools are loaded from `OllamaTools/`
- Safe tools execute concurrently; unsafe tools execute sequentially
- MCP server tools are exposed alongside local tools with `mcp_<server>_<tool>` names

---

## Data Flow

### Chat Request Flow

```text
1. A request arrives from the API or an adapter
   - direct REST call, Discord message, scheduled task, etc.

2. ai_pipeline_handler.py builds history
   - reads recent messages from discord_history.py (or adapter history backend)
   - prefixes each user turn as [DisplayName]: ...

3. AIPipelineRequest is created
   - messages, user_id, conversation_id
   - display_name, channel_name, guild_name, is_dm
   - memory/personality flags, images, provider options

4. context_builder.build_context()
   - slot 1: system prompt
   - slot 2: user_info tool message
   - resolve canonical user IDs via SAME_PERSON_AS
   - fetch deduplicated memory context for all linked identities

5. Provider executes chat
   - Ollama / OpenAI / Copilot / other provider
   - optional tool loop runs until a plain-text response is produced

6. Final response is normalized
   - mirrored [Nami]: prefixes are removed
   - response is sent back through the caller surface
```

### Memory Flow

```text
1. A conversation turn completes
   - user message + assistant reply become extraction input

2. MemoryExtractor builds the FACT_RETRIEVAL prompt
   - extracts memory type, memory_args, concepts, and locations
   - uses a dedicated extraction provider/model if configured

3. MemoryProcessor persists results
   - upserts any extracted Location nodes
   - stores memory nodes with IS_AUTHOR_OF links
   - links memories to concepts and OCCURRED_AT locations when matched

4. Future retrieval
   - ContextBuilder resolves canonical identities first
   - MemoryService retrieves relevant memories for each linked user ID
   - results are deduplicated by memory_id and formatted into one memory block

5. Ongoing lifecycle
   - hierarchy, consolidation, and decay adjust recall quality over time
```

---

## Configuration

### Minimal Shape

```yaml
api:
  host: "127.0.0.1"
  port: 11434

default_system_prompt: nami
default_provider: ollama
default_model: qwen3:32b

providers:
  ollama:
    url: http://localhost:11434
    max_tool_calls: 3

neo4j:
  uri: "bolt://neo4j:7687"
  user: "neo4j"
  pass: "${NEO4J_PASSWORD}"

memory:
  embedding_model: all-MiniLM-L6-v2
  embedding_dimension: 384
  similarity_threshold: 0.65
  extraction_provider: ollama
  extraction_model: llama3.2

adapters:
  discord:
    bridge_secret: CHANGE_ME_DISCORD_SECRET
    token: YOUR_DISCORD_BOT_TOKEN_HERE
    permitted_users: ["123456789012345678"]
    ai_channels: ["123456789012345679"]

sandbox:
  enabled: false
```

---

## Deployment

### Docker Compose

```bash
cp .env.example .env
docker compose up -d nami_neo4j nami_ai

docker compose --profile sandbox up -d nami_ai sandbox
docker compose --profile discord up -d nami_ai discord_bridge
docker compose --profile whatsapp up -d nami_ai whatsapp_bridge
```

The bundled Compose stack publishes the API on `127.0.0.1:11435`, Neo4j Browser on `127.0.0.1:7475`, and Bolt on `127.0.0.1:7688`.

### External Dependencies
- **Neo4j** - persistent graph memory backend
- **Ollama** - optional local inference backend
- **OpenAI / Copilot** - optional remote providers
- **Discord token** - required only if the Discord adapter is enabled

---

## Future Roadmap

1. **WhatsApp Adapter** - inbound handling to match the existing outbound path
2. **Admin / Memory UI** - inspect people, places, and memories without opening Neo4j Browser
3. **Tool Hot-Reload** - add or remove tools without restarting the service
4. **MCP Resource Support** - prompts/templates/resources in addition to tool calls
