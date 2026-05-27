# Tool Execution Flow Diagrams

## Tool Call Loop (inside AIPipeline)

```mermaid
sequenceDiagram
    participant AP as AIPipeline
    participant TE as execute_tool_loop
    participant Tool as Tool Function
    participant Ext as External API / Adapter

    AP->>TE: execute_tool_loop(provider, messages, tools, model, initial_response)

    loop Until no tool_calls or max_calls reached
        alt Has tool_calls
            TE->>TE: Check call count ≤ max_tool_calls (default 10)

            par Safe tools (concurrent)
                TE->>Tool: func(**args)
                Tool->>Ext: API / action.invoke call
                Ext-->>Tool: Result
                Tool-->>TE: tool_success(data) or tool_error(msg)
            and Safe tools (concurrent)
                TE->>Tool: func(**args)
                Tool-->>TE: tool_success(data)
            end

            Note over TE: Unsafe tools (writes/shell/scheduler)<br/>run sequentially

            TE->>TE: Append tool result messages to history
            TE->>AP: provider.chat(updated history)
            AP-->>TE: New response (may have more tool_calls)
        else No tool_calls or max reached
            TE-->>AP: Final response + tool_messages list
        end
    end
```

## Tool Registration and Loading

```mermaid
graph TB
    Start[Application Startup] --> TL[ToolLoader / tool_context]

    TL --> Scan[Scan OllamaTools/]
    Scan --> Files[Find *.py files]

    Files --> Load{For each file}

    Load --> Import[Import module]
    Import --> Check{Has get_tool?}

    Check -->|Yes| Call[Call get_tool()]
    Check -->|No| Skip1[Skip file]

    Call --> Schema[Returns tool schema dict]
    Schema --> Extract[Extract components]

    Extract --> C1[type: function]
    Extract --> C2[function: OpenAI-style schema]
    Extract --> C3[func: async callable]

    C1 --> Register[Register in g_data tools list]
    C2 --> Register
    C3 --> Register

    Register --> More{More files?}
    More -->|Yes| Load
    More -->|No| MCP[Load MCP tools via mcp_loader]

    MCP --> Remote[Connect to MCP servers]
    Remote --> Prefix[Prefix: mcp_servername_toolname]
    Prefix --> Register2[Register alongside local tools]

    Register2 --> Caps[Adapter capability tools registered<br/>per connection via AdapterWebSocketServer]

    Caps --> Ready[All tools available to AI]

    style Register fill:#c8e6c9
    style Register2 fill:#e1f5ff
    style Caps fill:#fff9c4
```

## Tool Execution States

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Extracting: AI returns tool_calls
    Extracting --> Executing: Extract tool name + args

    Executing --> ToolFound: Lookup in merged tools list
    Executing --> ToolNotFound: Unknown tool name

    ToolFound --> Safe: Read-only / no side effects
    ToolFound --> Unsafe: Write / shell / scheduler

    Safe --> Concurrent: Run with asyncio.gather
    Unsafe --> Sequential: Run one at a time

    Concurrent --> Success: Returns result
    Sequential --> Success: Returns result
    Concurrent --> Exception: Throws error
    Sequential --> Exception: Throws error

    Success --> Appending: Add to history
    Exception --> Appending: Add error to history
    ToolNotFound --> Appending: Add not-found to history

    Appending --> Retry: Call AI again with updated history
    Retry --> Idle

    Idle --> MaxReached: call_count > max_tool_calls
    MaxReached --> Forced: Force final response (tools=None)
    Forced --> [*]
```

## MCP Tool Integration

```mermaid
graph TB
    Config[config.yml] --> MCP[mcp_servers section]

    MCP --> S1[Server: filesystem]
    MCP --> S2[Server: github]
    MCP --> SN[Server: custom]

    S1 --> Launch1[Launch server process]
    S2 --> Launch2[Launch server process]
    SN --> LaunchN[Launch server process]

    Launch1 --> Connect1[JSON-RPC via stdio]
    Launch2 --> Connect2[JSON-RPC via stdio]
    LaunchN --> ConnectN[JSON-RPC via stdio]

    Connect1 --> List1[List available tools]
    Connect2 --> List2[List available tools]
    ConnectN --> ListN[List available tools]

    List1 --> Prefix1[Prefix: mcp_filesystem_]
    List2 --> Prefix2[Prefix: mcp_github_]
    ListN --> PrefixN[Prefix: mcp_custom_]

    Prefix1 --> Register[Register with local tools in g_data]
    Prefix2 --> Register
    PrefixN --> Register

    Register --> Available[Available to AI same as local tools]

    AI[AI calls tool] --> Route{Tool name prefix?}
    Route -->|mcp_| Remote[Route to MCP server via JSON-RPC]
    Route -->|adapter_name_| Action[Route to adapter via action.invoke]
    Route -->|other| Local[Execute local Python function]

    Remote --> Result[Return result string]
    Action --> Result
    Local --> Result

    style Register fill:#c8e6c9
    style Remote fill:#e1f5ff
    style Action fill:#fff9c4
```

## Tool Call Limit Guard

```mermaid
graph TB
    Start[AI Response] --> Check{Has tool_calls?}

    Check -->|No| Done[Return final response]
    Check -->|Yes| Count[Increment call_count]

    Count --> Limit{call_count ≤ max_tool_calls?}

    Limit -->|Yes| Execute[Execute tools<br/>safe=concurrent, unsafe=sequential]
    Limit -->|No| Force[Force final response]

    Execute --> Append[Append results to history]
    Append --> CallAI[Call AI again]
    CallAI --> Start

    Force --> Message[Add limit exceeded system message]
    Message --> Final[Call AI with tools=None]
    Final --> Done

    style Limit fill:#fff9c4
    style Force fill:#ffcccc
    style Execute fill:#c8e6c9
```
