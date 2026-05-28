# AI Provider Architecture Diagrams

## Provider Registry Pattern

```mermaid
graph TB
    App[Application] --> PR[ProviderRegistry]

    PR --> Cache[Provider Cache]
    Cache --> P1[Ollama: Instance]
    Cache --> P2[OpenAI: Instance]
    Cache --> P3[Copilot: Instance]

    Request[Chat Request] --> Parse[Parse model string]
    Parse --> Format[provider/model]

    Format --> Get[get_or_create_provider]
    Get --> Check{Provider cached?}

    Check -->|Yes| Return1[Return cached instance]
    Check -->|No| Create[Create new instance]

    Create --> Config[Load config from config.yml]
    Config --> Init[Initialize provider]
    Init --> Store[Store in cache]
    Store --> Return2[Return instance]

    Return1 --> Use[Use for chat]
    Return2 --> Use

    style Cache fill:#e1f5ff
    style Return1 fill:#c8e6c9
```

## Provider Inheritance Hierarchy

```mermaid
classDiagram
    class AIProvider {
        <<abstract>>
        +config dict
        +capabilities set
        +chat(messages, tools, kwargs) ChatResponse
        +chat_stream(messages, tools, kwargs) AsyncIterator
        +list_models() list
        +get_provider_name() str
        +normalize_messages(messages) list
        +ensure_capabilities(model_name) void
        +supports_tools() bool
        +supports_vision() bool
        +supports_streaming() bool
    }

    class OllamaProvider {
        +url str
        +default_model str
        +client OllamaClient
        +chat(messages, tools, kwargs) ChatResponse
        +chat_stream(messages, tools, kwargs) AsyncIterator
        +list_models() list
        +get_provider_name() str
        +query_model_capabilities(model) set
        +ensure_capabilities(model_name) void
    }

    class OpenAIProvider {
        +api_key str
        +default_model str
        +client AsyncOpenAI
        +chat(messages, tools, kwargs) ChatResponse
        +chat_stream(messages, tools, kwargs) AsyncIterator
        +list_models() list
        +get_provider_name() str
    }

    class CopilotProvider {
        +api_key str
        +default_model str
        +client AsyncOpenAI
        +chat(messages, tools, kwargs) ChatResponse
        +chat_stream(messages, tools, kwargs) AsyncIterator
        +list_models() list
        +get_provider_name() str
    }

    AIProvider <|-- OllamaProvider
    AIProvider <|-- OpenAIProvider
    AIProvider <|-- CopilotProvider
```

## Thinking Mode Resolution

```mermaid
graph TB
    Input[incoming message + config] --> Resolve[resolve_thinking_mode]

    Resolve --> Override{think_override set?}
    Override -->|True| UseThink[Use thinking model]
    Override -->|False| NoThink[Skip thinking]
    Override -->|None auto| Trigger{Trigger words in message?}

    Trigger -->|Yes| UseThink
    Trigger -->|No| NoThink

    UseThink --> Model{thinking_model configured?}
    Model -->|Yes| Switch[Switch to thinking model]
    Model -->|No| Same[Keep default model]

    Switch --> Chat[provider.chat(model=thinking_model, think=True)]
    Same --> Chat2[provider.chat(model=default, think=True)]
    NoThink --> Chat3[provider.chat(model=default)]

    Chat --> Response[ChatResponse with thinking field]
    Chat2 --> Response
    Chat3 --> Response2[ChatResponse no thinking]

    style UseThink fill:#fff9c4
    style Switch fill:#e1f5ff
```

## Message Normalization Flow

```mermaid
graph TB
    Input[Message objects] --> Normalize[normalize_messages]

    Normalize --> Loop{For each message}

    Loop --> Extract[Extract fields]
    Extract --> Role[role]
    Extract --> Content[content]
    Extract --> Name[name?]
    Extract --> Tools[tool_calls?]
    Extract --> Images[images?]

    Role --> Dict[Create dict]
    Content --> Dict

    Name --> Check1{include_name?}
    Check1 -->|Yes| Add1[Add name]
    Check1 -->|No| Skip1[Skip]

    Tools --> Check2{Has tool_calls?}
    Check2 -->|Yes| Add2[Add tool_calls]
    Check2 -->|No| Skip2[Skip]

    Images --> Check3{Has images?}
    Check3 -->|Yes| Add3[Add images list]
    Check3 -->|No| Skip3[Skip]

    Dict --> More{More messages?}
    Add1 --> More
    Add2 --> More
    Add3 --> More
    Skip1 --> More
    Skip2 --> More
    Skip3 --> More

    More -->|Yes| Loop
    More -->|No| Return[Return normalized list]

    style Dict fill:#e1f5ff
```

## ChatResponse Data Flow

```mermaid
graph LR
    Provider[Provider API] --> Raw[Raw Response]

    Raw --> Extract{Extract fields}

    Extract --> Content[content: str]
    Extract --> Tools[tool_calls: list or None]
    Extract --> Model[model: str]
    Extract --> Reason[finish_reason: str]
    Extract --> Usage[usage: dict or None]
    Extract --> Thinking[thinking: str or None]

    Content --> CR[ChatResponse]
    Tools --> CR
    Model --> CR
    Reason --> CR
    Usage --> CR
    Thinking --> CR

    CR --> Serialize{Serialization}

    Serialize --> API[REST API response<br/>ChatCompletionResponse]
    Serialize --> WS[WebSocket response.ready event]

    API --> Client[HTTP Client]
    WS --> Bridge[Node.js Bridge → Platform]

    style CR fill:#c8e6c9
    style Thinking fill:#fff9c4
```
