# Chat Adapter Flow Diagrams

## External Adapter Message Flow (WebSocket Protocol)

External chat adapters (Discord, WhatsApp, etc.) are independent Node.js services
that connect to Nami via a persistent WebSocket. All message routing is event-driven.

```mermaid
sequenceDiagram
    participant Bridge as Node.js Bridge<br/>(adapters/discord_bridge / adapters/whatsapp_bridge)
    participant WS as AdapterWebSocketServer<br/>/api/ws/adapter
    participant EB as EventBus
    participant PH as AIPipelineHandler
    participant AP as AIPipeline

    Bridge->>WS: WebSocket connect<br/>?name=discord&secret=<bridge_secret>

    Bridge->>WS: capabilities.register<br/>{ actions: [tool schemas] }
    WS-->>Bridge: capabilities.ack<br/>{ config: { ...adapter config } }

    Note over Bridge: Init platform client (deferred until ack)

    Bridge->>WS: message.received<br/>{ conversation_id, user_id, user_name,<br/>  display_name, content, is_dm,<br/>  history[], image_urls[], channel_name, guild_name }

    WS->>EB: publish(message.received)
    EB->>PH: _on_message_received(event)

    Note over PH: Acquire per-conversation lock

    PH->>AP: ai_pipeline.run(AIPipelineRequest)

    alt AI calls adapter action tool
        AP->>WS: action.invoke { call_id, action, params }
        WS->>Bridge: action.invoke (30 s timeout)
        Bridge-->>WS: action.result { call_id, data }
        WS-->>AP: Tool result resolved
    end

    AP-->>PH: AIPipelineResult

    PH->>EB: publish(response.ready)<br/>{ conversation_id, content }

    EB->>WS: _on_response_ready(event)
    WS->>Bridge: response.ready { conversation_id, content }

    Note over Bridge: content == "<ignore>" → do not reply
```

## WebSocket Event Protocol

```mermaid
graph LR
    subgraph "Bridge → Nami"
        E1[capabilities.register<br/>actions: tool schemas]
        E2[message.received<br/>full message + history]
        E3[action.result<br/>call_id + data]
        E4[ping]
    end

    subgraph "Nami → Bridge"
        R1[capabilities.ack<br/>adapter config]
        R2[response.ready<br/>conversation_id + content]
        R3[action.invoke<br/>call_id + action + params]
        R4[send.message<br/>proactive channel post]
        R5[send.dm<br/>proactive DM]
        R6[status.update<br/>typing / tool hint]
        R7[pong]
    end

    E1 -.->|triggers| R1
    E2 -.->|triggers| R2
    E3 -.->|resolves| R3
    E4 -.->|triggers| R7

    style E1 fill:#e1f5ff
    style E2 fill:#e1f5ff
    style E3 fill:#e1f5ff
    style E4 fill:#e1f5ff
    style R1 fill:#c8e6c9
    style R2 fill:#c8e6c9
    style R3 fill:#fff9c4
    style R4 fill:#fff9c4
    style R5 fill:#fff9c4
```

## Capability / Adapter Tool System

```mermaid
graph TB
    Bridge[Node.js Bridge] -->|capabilities.register| WS[AdapterWebSocketServer]

    WS --> Schemas[OpenAI-style tool schemas<br/>e.g. add_reaction, create_thread]
    Schemas --> Prefix[Prefix: adapter_name_action_name<br/>e.g. discord_add_reaction]
    Prefix --> CapTools[Capability tool dicts<br/>with func closure → action.invoke]

    PH[AIPipelineHandler] --> Lookup[Look up adapter tools<br/>for originating adapter]
    Lookup --> Request[Inject into AIPipelineRequest<br/>.additional_tools]
    Request --> AP[AIPipeline merges with global tools]

    AP -->|AI calls discord_add_reaction| Route[Route to action.invoke]
    Route --> Bridge

    style Prefix fill:#e1f5ff
    style CapTools fill:#c8e6c9
```

## Response Handling in Bridges

```mermaid
graph TB
    WS[response.ready event] --> Bridge[Node.js Bridge receives content]
    Bridge --> Ignore{content == "&lt;ignore&gt;"?}

    Ignore -->|Yes| Drop[Do not reply]
    Ignore -->|No| Length{Length check}

    Length -->|≤ 2000 chars| Single[Send single message]
    Length -->|> 2000 chars| Chunk[Split and send chunks]

    Chunk --> Typing[Show typing indicator]
    Typing --> Send[Send each chunk]

    style Drop fill:#ffcccc
    style Single fill:#c8e6c9
    style Send fill:#c8e6c9
```

## Adapter Decision Logic (Bridge-side)

```mermaid
graph TB
    Message[Incoming Platform Message] --> Bot{Is bot / own message?}

    Bot -->|Yes| Ignore1[❌ Ignore]
    Bot -->|No| AIChannel{Is AI channel?}

    AIChannel -->|Yes| Respond1[✅ Send message.received]
    AIChannel -->|No| DM{Is DM?}

    DM -->|Yes| Permitted1{From permitted user?}
    DM -->|No| Mention{Bot mentioned?}

    Permitted1 -->|Yes| Respond2[✅ Send message.received]
    Permitted1 -->|No| Ignore3[❌ Ignore]

    Mention -->|Yes| Permitted2{From permitted user?}
    Mention -->|No| Ignore4[❌ Ignore]

    Permitted2 -->|Yes| Respond3[✅ Send message.received]
    Permitted2 -->|No| Ignore5[❌ Ignore]

    style Respond1 fill:#c8e6c9
    style Respond2 fill:#c8e6c9
    style Respond3 fill:#c8e6c9
    style Ignore1 fill:#ffcccc
    style Ignore3 fill:#ffcccc
    style Ignore4 fill:#ffcccc
    style Ignore5 fill:#ffcccc
```

## Unified Architecture Overview

```mermaid
graph TB
    subgraph "External Bridges (Node.js)"
        DB[adapters/discord_bridge]
        WB[adapters/whatsapp_bridge]
    end

    subgraph "Nami AI (Python / FastAPI)"
        WS[AdapterWebSocketServer<br/>/api/ws/adapter]
        EB[EventBus]
        PH[AIPipelineHandler]
        AP[AIPipeline]
        CB[ContextBuilder]
        MS[MemoryService]
        PR[ProviderRegistry]
    end

    subgraph "REST Clients"
        REST[POST /v1/chat/completions]
    end

    DB <-->|WebSocket| WS
    WB <-->|WebSocket| WS
    REST -->|HTTP| WS

    WS --> EB
    EB --> PH
    PH --> AP
    AP --> CB
    AP --> MS
    AP --> PR

    subgraph "Unified Identity"
        MS --> MDB[(Neo4j<br/>Shared memory pool)]
        Note1[✅ Same personality across all platforms]
    end

    style WS fill:#e1f5ff
    style EB fill:#fff4e1
    style PH fill:#c8e6c9
    style MDB fill:#ffe1f5
```

## Cross-Platform Identity

```mermaid
graph TB
    P1["scoped_id: discord:123456789"]
    P2["scoped_id: whatsapp:+15551234567"]
    P3["scoped_id: api:<username>"]

    P1 ---|SAME_PERSON_AS| P2
    P2 ---|SAME_PERSON_AS| P3

    Request[New request from discord:123456789] --> Resolve[resolve_canonical_users]
    Resolve --> Canonical["All linked IDs:<br/>discord:123456789<br/>whatsapp:+15551234567<br/>api:<username>"]
    Canonical --> Retrieve[get_formatted_memories_multi_user]
    Retrieve --> Dedupe[Deduplicate repeated memory hits]
    Dedupe --> Context[Inject one combined memory block → prompt]

    style Canonical fill:#e1f5ff
    style Context fill:#c8e6c9
```
