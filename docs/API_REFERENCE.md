# API Reference

Complete reference for the Personality Proxy API. The API is compatible with Ollama's API format with extensions for personality and memory features.

## Base URL

```
http://localhost:11434
```

Default port is `11434` for Ollama compatibility. Configure in `config.yml`.

## Authentication

Currently no authentication required. Add authentication middleware as needed for production.

## Endpoints

### Chat Completion

Create a chat completion with personality and memory support.

**POST** `/api/chat`

**Request Body:**

```json
{
  "model": "string (required)",
  "messages": [
    {
      "role": "string (required)",
      "content": "string (required)",
      "images": ["string (optional)"],
      "tool_calls": [{} (optional)]
    }
  ],
  "stream": "boolean (optional, default: false)",
  "tools": [{} (optional)],
  "format": "string (optional)",
  "options": {} (optional),

  // Personality Proxy extensions
  "user_id": "string (optional)",
  "conversation_id": "string (optional)",
  "enable_memory": "boolean (optional, default: true)",
  "enable_personality": "boolean (optional, default: true)"
}
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `model` | string | Yes | Model name (from provider) |
| `messages` | array | Yes | Array of message objects |
| `messages[].role` | string | Yes | One of: `system`, `user`, `assistant`, `tool` |
| `messages[].content` | string | Yes | Message content |
| `stream` | boolean | No | Enable streaming response |
| `tools` | array | No | Available tools for function calling |
| `user_id` | string | No | User identifier for memory/personalization |
| `conversation_id` | string | No | Conversation identifier for context |
| `enable_memory` | boolean | No | Enable Neo4j memory system (default: true) |
| `enable_personality` | boolean | No | Include personality prompt (default: true) |

**Response (Non-Streaming):**

```json
{
  "model": "llama2",
  "created_at": "2024-01-17T12:00:00Z",
  "message": {
    "role": "assistant",
    "content": "Hello! I'm Nami, your AI assistant...",
    "tool_calls": null
  },
  "done": true,
  "total_duration": 1500000000,
  "load_duration": 100000000,
  "prompt_eval_count": 50,
  "eval_count": 150
}
```

**Response (Streaming):**

When `stream: true`, returns newline-delimited JSON chunks:

```json
{"model":"llama2","created_at":"...","message":{"role":"assistant","content":"Hello"},"done":false}
{"model":"llama2","created_at":"...","message":{"role":"assistant","content":"!"},"done":false}
{"model":"llama2","created_at":"...","message":{"role":"assistant","content":""},"done":true}
```

**Examples:**

```bash
# Simple chat
curl http://localhost:11434/api/chat -d '{
  "model": "llama2",
  "messages": [
    {"role": "user", "content": "Hello!"}
  ]
}'

# With memory and personality
curl http://localhost:11434/api/chat -d '{
  "model": "llama2",
  "messages": [
    {"role": "user", "content": "What did we discuss earlier?"}
  ],
  "user_id": "alice",
  "conversation_id": "daily_chat",
  "enable_memory": true,
  "enable_personality": true
}'

# Streaming
curl http://localhost:11434/api/chat -d '{
  "model": "llama2",
  "messages": [
    {"role": "user", "content": "Tell me a story"}
  ],
  "stream": true
}'
```

---

### Generate Completion

Generate a completion from a prompt (simpler than chat).

**POST** `/api/generate`

**Request Body:**

```json
{
  "model": "string (required)",
  "prompt": "string (required)",
  "stream": "boolean (optional, default: false)",
  "system": "string (optional)",
  "options": {} (optional),

  // Personality Proxy extensions
  "user_id": "string (optional)",
  "enable_memory": "boolean (optional, default: true)",
  "enable_personality": "boolean (optional, default: true)"
}
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `model` | string | Yes | Model name |
| `prompt` | string | Yes | Prompt text |
| `stream` | boolean | No | Enable streaming |
| `system` | string | No | System prompt (overrides personality if set) |
| `user_id` | string | No | User identifier |
| `enable_memory` | boolean | No | Enable memory system |
| `enable_personality` | boolean | No | Use personality prompt |

**Response:**

Same format as `/api/chat`.

**Example:**

```bash
curl http://localhost:11434/api/generate -d '{
  "model": "llama2",
  "prompt": "Why is the sky blue?",
  "user_id": "alice"
}'
```

---

### List Models

Get available models from the current AI provider.

**GET** `/api/tags`

**Response:**

```json
{
  "models": [
    {
      "name": "llama2",
      "modified_at": "2024-01-17T12:00:00Z",
      "size": 0,
      "digest": ""
    },
    {
      "name": "mistral",
      "modified_at": "2024-01-17T12:00:00Z",
      "size": 0,
      "digest": ""
    }
  ]
}
```

**Example:**

```bash
curl http://localhost:11434/api/tags
```

---

### Version Info

Get API version and capabilities.

**GET** `/` or **GET** `/api/version`

**Response:**

```json
{
  "version": "2.0.0",
  "name": "Personality Proxy API",
  "ollama_compatible": true,
  "provider": "ollama",
  "features": ["personality", "memory", "tools"]
}
```

**Example:**

```bash
curl http://localhost:11434/
```

---

### Health Check

Check API health and status.

**GET** `/health`

**Response:**

```json
{
  "status": "healthy",
  "provider": "ollama",
  "provider_available": true,
  "memory_db_available": true,
  "memory_entries": 1234
}
```

**Example:**

```bash
curl http://localhost:11434/health
```

---

## OpenAI Compatibility

The API also supports OpenAI's `/v1/chat/completions` endpoint format:

**POST** `/v1/chat/completions`

This is an alias to `/api/chat` and accepts the same parameters.

**Example:**

```python
import openai

openai.api_base = "http://localhost:11434/v1"
openai.api_key = "not-needed"

response = openai.ChatCompletion.create(
    model="llama2",
    messages=[
        {"role": "user", "content": "Hello!"}
    ]
)
```

---

## Personality Proxy Extensions

These additional parameters enhance the standard Ollama API:

### user_id

**Type:** `string`
**Optional**

Identifies the user for memory personalization. When provided:
- Memories are associated with this user
- Retrieved memories are filtered by user
- Conversation history is tracked per user

```json
{
  "user_id": "alice@example.com"
}
```

### conversation_id

**Type:** `string`
**Optional**

Identifies the conversation thread. When provided:
- Messages are grouped by conversation
- History retrieval is scoped to this conversation
- Multiple conversations per user are supported

```json
{
  "conversation_id": "daily_chat_2024_01_17"
}
```

### enable_memory

**Type:** `boolean`
**Default:** `true`

Controls whether the memory system is used:
- `true`: Retrieve relevant memories and store new ones
- `false`: Skip memory operations (faster responses)

```json
{
  "enable_memory": false
}
```

### enable_personality

**Type:** `boolean`
**Default:** `true`

Controls whether the personality prompt is injected:
- `true`: Add system prompt from `system_prompt/*.md`
- `false`: Use only provided messages

```json
{
  "enable_personality": false
}
```

---

## Tool Calling

The API supports tool/function calling when the provider supports it.

### Request with Tools

```json
{
  "model": "llama2",
  "messages": [
    {"role": "user", "content": "What's the weather?"}
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get current weather",
        "parameters": {
          "type": "object",
          "properties": {
            "location": {
              "type": "string",
              "description": "City name"
            }
          },
          "required": ["location"]
        }
      }
    }
  ]
}
```

### Response with Tool Calls

```json
{
  "model": "llama2",
  "message": {
    "role": "assistant",
    "content": "",
    "tool_calls": [
      {
        "function": {
          "name": "get_weather",
          "arguments": {
            "location": "San Francisco"
          }
        }
      }
    ]
  },
  "done": true
}
```

---

## Error Responses

### 400 Bad Request

Invalid request parameters.

```json
{
  "error": "Invalid request: missing required field 'model'"
}
```

### 500 Internal Server Error

Server error (provider failure, etc).

```json
{
  "error": "Provider error: connection refused"
}
```

### 503 Service Unavailable

Service not ready (provider not initialized, etc).

```json
{
  "error": "AI provider not initialized"
}
```

---

## Rate Limiting

Currently no rate limiting is implemented. Add rate limiting middleware as needed:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.post("/api/chat")
@limiter.limit("10/minute")
async def chat(request: Request, ...):
    ...
```

---

## CORS

CORS is enabled for all origins by default. Restrict in production:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://your-domain.com"],
    allow_credentials=True,
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)
```

---

## Client Libraries

### Python (ollama)

```python
from ollama import Client

client = Client(host='http://localhost:11434')

response = client.chat(
    model='llama2',
    messages=[{'role': 'user', 'content': 'Hello'}],
    options={'user_id': 'alice'}
)
```

### Python (openai)

```python
import openai

openai.api_base = "http://localhost:11434/v1"
openai.api_key = "not-needed"

response = openai.ChatCompletion.create(
    model="llama2",
    messages=[{"role": "user", "content": "Hello"}]
)
```

### JavaScript/TypeScript

```typescript
const response = await fetch('http://localhost:11434/api/chat', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    model: 'llama2',
    messages: [{role: 'user', content: 'Hello'}],
    user_id: 'alice'
  })
});

const data = await response.json();
console.log(data.message.content);
```

### cURL

```bash
curl -X POST http://localhost:11434/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama2",
    "messages": [{"role": "user", "content": "Hello"}],
    "user_id": "alice"
  }'
```

---

## See Also

- [Quick Start](QUICKSTART.md) - Get started quickly
- [Providers](PROVIDERS.md) - AI provider configuration
- [Memory System](MEMORY_SYSTEM.md) - Memory configuration
- [Tools](TOOLS.md) - Tool development

---

Need help? [Open an issue](https://github.com/oOHiyoriOo/nami_ai/issues) on GitHub.
