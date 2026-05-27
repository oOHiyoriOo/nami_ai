# Nami AI Data Flow Diagrams

Visual documentation of data flows, transformations, and architectural patterns in the Nami AI system.

## Contents

### [01 - Message Flow](./01_message_flow.md)
- REST API request flow (client → EventBus → AIPipeline → response)
- Data transformation pipeline (APIMessage → event dict → AIPipelineRequest → Message)
- ChatCompletionRequest field journey
- Context building slot order (system prompt → user_info → memories → history)

### [02 - Memory Flow](./02_memory_flow.md)
- Memory extraction pipeline (background task after each turn)
- Memory retrieval flow (multi-user, SAME_PERSON_AS resolution)
- Memory hierarchy system (transient → short-term → episodic → semantic → core)
- Neo4j data model (PERSON, MEMORY, CONCEPT, LOCATION)
- Cross-platform identity resolution
- Memory decay scoring

### [03 - Chat Adapter Flow](./03_adapter_flow.md)
- External adapter WebSocket protocol (capabilities.register / message.received / response.ready)
- WS event reference (inbound and outbound)
- Capability / adapter tool system (per-adapter actions as AI tools)
- Response handling in bridges (chunking, `<ignore>` suppression)
- Adapter decision logic (AI channel / DM / mention)
- Unified architecture overview

### [04 - Tool Execution](./04_tool_execution.md)
- Tool call loop inside AIPipeline (safe=concurrent, unsafe=sequential)
- Tool registration and loading (OllamaTools + MCP + adapter capabilities)
- Tool execution state machine
- MCP tool integration (JSON-RPC stdio, prefix routing)
- Tool call limit guard

### [05 - Provider Architecture](./05_provider_architecture.md)
- Provider registry pattern
- Provider inheritance hierarchy (AIProvider → Ollama / OpenAI / Copilot)
- Message normalization flow
- ChatResponse data flow

### [06 - Data Structures](./06_data_structures.md)
- Message format comparison (APIMessage / event dict / AIPipelineRequest / Message)
- Global registry (g_data) full key list
- AIPipelineResult fields

## Legend

### Colors
- 🟢 **Green** — Successful/complete data flow
- 🟡 **Yellow** — Optional or conditional path
- 🔴 **Red** — Error path or forced fallback
- 🔵 **Blue** — Key component or transformation
- 🟣 **Purple** — External service

### Symbols
- ✅ — Feature working as expected
- ❌ — Error / ignored path
- ⚠️ — Warning or edge case
- 🔄 — Transformation point

## How to Read These Diagrams

### Sequence Diagrams
Show the order of operations and interactions between components over time. Read top to bottom.

### Flow Charts
Show decision logic and process flows. Follow arrows to trace data paths.

### State Diagrams
Show different states a component can be in and how it transitions between them.

### Class Diagrams
Show the structure of classes and their relationships.

### Entity-Relationship Diagrams
Show data models and database schemas.

## Viewing Mermaid Diagrams

These diagrams use [Mermaid.js](https://mermaid.js.org/) syntax and can be viewed in:

- **GitHub / Gitea** — Automatically renders in markdown files
- **VS Code** — Install "Markdown Preview Mermaid Support" extension
- **Browser** — Use [Mermaid Live Editor](https://mermaid.live/)
- **Obsidian** — Native mermaid support
