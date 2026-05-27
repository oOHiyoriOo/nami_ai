# Memory System Flow Diagrams

## Memory Extraction Pipeline

```mermaid
sequenceDiagram
    participant Msg as Message Event
    participant MP as MemoryProcessor
    participant ME as MemoryExtractor
    participant Provider as Extraction Provider
    participant MDB as MemoryDb (Neo4j)

    Msg->>MP: process_memories(user_id, content)
    Note over MP: Background task after a chat turn

    MP->>ME: extract_memories(message_content, user_name, timestamp)
    ME->>MDB: search(query, top_k=5)
    MDB-->>ME: related memory context

    ME->>ME: Build FACT_RETRIEVAL prompt
    Note over ME: Includes memory type, concepts, and locations

    ME->>Provider: chat(messages)
    Provider-->>ME: JSON memory candidates

    alt Parsed successfully
        ME-->>MP: ExtractedMemory[]
        loop Each extracted memory
            MP->>MDB: add_location(location_id, name, description)
            MP->>MDB: add_memory(user_id, user_name, memory_type, memory_args, location_id)
            Note over MDB: Creates IS_AUTHOR_OF, REFERS_TO_CONCEPT, and OCCURRED_AT links
        end
    else Parse failure
        ME->>ME: Retry extraction (max 3)
    end
```

## Memory Retrieval Flow

```mermaid
graph TB
    Start[Context Builder] --> System[Add system prompt]
    System --> UserInfo[Add user_info tool message (slot 2)]
    UserInfo --> Query[Get last user message]
    Query --> Resolve[Resolve canonical identities<br/>resolve_canonical_users(user_id)]
    Resolve --> Multi[MemoryService.get_formatted_memories_multi_user]
    Multi --> Search[Retrieve memories for each linked scoped ID]
    Search --> Dedupe[Deduplicate by memory_id]
    Dedupe --> Format[Format memories as one system block]
    Format --> Prompt[Return enhanced prompt to provider]
```

## Memory Hierarchy System

```mermaid
graph LR
    subgraph "Memory Tiers"
        T1[Transient<br/>Working Memory]
        T2[Short-term<br/>Recent Context]
        T3[Episodic<br/>Events]
        T4[Semantic<br/>Knowledge]
        T5[Core<br/>Identity]
    end

    T1 -->|Promotion| T2
    T2 -->|Consolidation| T3
    T3 -->|Abstraction| T4
    T4 -->|Core Facts| T5

    T1 -.->|Decay fast| Forget1[Forgotten]
    T2 -.->|Decay medium| Forget2[Forgotten]
    T3 -.->|Decay slow| Forget3[Archived]

    Query[Query] --> SearchAll{Search all tiers}
    SearchAll --> T1
    SearchAll --> T2
    SearchAll --> T3
    SearchAll --> T4
    SearchAll --> T5

    T1 --> Score[Relevance score]
    T2 --> Score
    T3 --> Score
    T4 --> Score
    T5 --> Score

    Score --> Rank[Rank by relevance + decay]
    Rank --> TopK[Return top_k]
```

## Memory Data Model

```mermaid
erDiagram
    PERSON {
        string id
        string name
        string nickname
    }

    LOCATION {
        string location_id
        string name
        string description
    }

    MEMORY {
        string id
        float importance
        datetime creationTimestamp
        vector summaryEmbeddingVector
    }

    CONCEPT {
        string name
    }

    PERSON ||--o{ MEMORY : IS_AUTHOR_OF
    MEMORY }o--o{ CONCEPT : REFERS_TO_CONCEPT
    MEMORY }o--|| LOCATION : OCCURRED_AT
    MEMORY }o--|| PERSON : IS_ABOUT
    PERSON o{--o{ PERSON : SAME_PERSON_AS
```

## Cross-Platform Identity Resolution

```mermaid
graph TB
    Discord[Person: discord:123]
    WhatsApp[Person: whatsapp:+15551234567]
    API[Person: api:<username>]

    Discord ---|SAME_PERSON_AS| WhatsApp
    WhatsApp ---|SAME_PERSON_AS| API

    Request[New request from discord:123] --> Resolve[resolve_canonical_users]
    Resolve --> Canonical[discord:123<br/>whatsapp:+15551234567<br/>api:<username>]
    Canonical --> Retrieve[Retrieve memories per linked ID]
    Retrieve --> Dedupe[Deduplicate repeated memory hits]
    Dedupe --> Context[Inject one combined memory block]
```

## Memory Decay Scoring

```mermaid
graph LR
    Memory[Memory] --> Factors{Scoring factors}

    Factors --> F1[Base similarity]
    Factors --> F2[Time decay]
    Factors --> F3[Access frequency]
    Factors --> F4[Importance]
    Factors --> F5[Hierarchy / related context]

    F1 --> Combine[Weighted combination]
    F2 --> Combine
    F3 --> Combine
    F4 --> Combine
    F5 --> Combine

    Combine --> Score[Final relevance score]
    Score --> Threshold{Above threshold?}
    Threshold -->|Yes| Include[Inject into prompt]
    Threshold -->|No| Exclude[Discard]
```
