# Conversation Tracking

The Personality Proxy API supports conversation tracking via `conversation_id` to maintain separate conversation contexts.

## Overview

**Problem:** Multiple conversations (different channels, threads, topics) getting mixed together.

**Solution:** Each conversation gets a unique ID that scopes the context.

## How It Works

### Request

Include `conversation_id` in your chat request:

```json
{
  "model": "ollama/llama2",
  "messages": [...],
  "user_id": "alice",
  "conversation_id": "discord_channel_123456"
}
```

### Response

The API returns the `conversation_id` back to you:

```json
{
  "model": "ollama/llama2",
  "message": {...},
  "conversation_id": "discord_channel_123456",
  "user_id": "alice",
  "done": true
}
```

### Auto-Generation

If you **don't provide** a `conversation_id`, the API generates one:

```json
// Request (no conversation_id)
{
  "model": "ollama/llama2",
  "messages": [...]
}

// Response (generated conversation_id)
{
  "model": "ollama/llama2",
  "message": {...},
  "conversation_id": "conv_a1b2c3d4e5f6g7h8",  // Auto-generated
  "user_id": "anonymous",
  "done": true
}
```

**Important:** Save the returned `conversation_id` and use it in subsequent requests to continue the same conversation!

## Use Cases

### 1. Discord Channels

Different Discord channels = different conversations:

```python
# Channel #general
response = await client.chat(
    model="ollama/llama2",
    messages=[...],
    options={
        'user_id': 'discord_alice',
        'conversation_id': 'discord_channel_12345'  # #general
    }
)

# Channel #random
response = await client.chat(
    model="ollama/llama2",
    messages=[...],
    options={
        'user_id': 'discord_alice',
        'conversation_id': 'discord_channel_67890'  # #random
    }
)
```

Same user, different conversation contexts!

### 2. Discord Threads

Threads within a channel = separate conversations:

```python
# Main channel
conversation_id = 'discord_channel_12345'

# Thread 1
conversation_id = 'discord_thread_11111'

# Thread 2
conversation_id = 'discord_thread_22222'
```

### 3. Web Chat Sessions

Different browser tabs = different sessions:

```python
# Tab 1
conversation_id = 'web_session_abc123'

# Tab 2
conversation_id = 'web_session_def456'
```

### 4. Topic-Based Conversations

Organize by topic:

```python
# Discussing Python
conversation_id = 'topic_python_2024_01'

# Discussing JavaScript
conversation_id = 'topic_javascript_2024_01'
```

## Conversation ID Format

**Recommended formats:**

```
discord_channel_{channel_id}      # Discord channels
discord_thread_{thread_id}        # Discord threads
slack_channel_{channel_id}        # Slack channels
telegram_chat_{chat_id}           # Telegram chats
web_session_{session_id}          # Web sessions
topic_{topic}_{date}              # Topic-based
user_{user_id}_private            # Private 1:1
```

**Requirements:**
- Use only alphanumeric characters, underscores, and dashes
- Keep it meaningful (helps debugging)
- Max length: 128 characters (recommended)

## Context Scoping

The `conversation_id` tells the AI:

```
Context: You are in user ID 'alice' and conversation 'discord_channel_12345'
```

This allows the AI to:
- Understand which conversation it's in
- Reference the conversation context ("in this channel", "earlier in this chat")
- Keep conversations separate even for the same user

## Memory and Conversation ID

Currently, memories are scoped by `user_id` only (global to the user).

**Future enhancement:** Memory could be filtered by conversation_id for conversation-specific memories.

```python
# Current behavior
memories = get_memories(user_id='alice')
# Returns ALL memories for Alice across all conversations

# Potential future
memories = get_memories(
    user_id='alice',
    conversation_id='discord_channel_12345'
)
# Returns only memories from this specific channel
```

## Best Practices

### 1. Always Provide conversation_id

```python
# Good
response = await client.chat(
    model="ollama/llama2",
    messages=[...],
    options={
        'user_id': 'alice',
        'conversation_id': 'discord_channel_12345'
    }
)

# Not ideal (will auto-generate)
response = await client.chat(
    model="ollama/llama2",
    messages=[...]
)
```

### 2. Save and Reuse

```python
# First message
response1 = await client.chat(...)
conversation_id = response1['conversation_id']  # Save this!

# Continue conversation
response2 = await client.chat(
    ...,
    options={'conversation_id': conversation_id}  # Reuse it
)
```

### 3. Include User ID

Combine `user_id` and `conversation_id` for best results:

```python
response = await client.chat(
    model="ollama/llama2",
    messages=[...],
    options={
        'user_id': 'alice',              # Who is speaking
        'conversation_id': 'channel_123'  # Where they're speaking
    }
)
```

### 4. Use Meaningful IDs

```python
# Good - clear what this is
conversation_id = 'discord_channel_general_123456'

# Bad - unclear
conversation_id = 'conv_123'
```

## Discord Bot Example

```python
@client.event
async def on_message(message):
    # Extract user and conversation from Discord
    user_id = f"discord_{message.author.id}"
    conversation_id = f"discord_channel_{message.channel.id}"

    # If in thread
    if isinstance(message.channel, discord.Thread):
        conversation_id = f"discord_thread_{message.channel.id}"

    # Call API
    response = await api_client.chat(
        model="ollama/llama2",
        messages=[...],
        options={
            'user_id': user_id,
            'conversation_id': conversation_id,
            'enable_memory': True
        }
    )

    # Response includes the conversation_id
    # (Can be logged, stored, etc.)
    logging.info(f"Response for {response['conversation_id']}")
```

## Streaming Responses

Conversation IDs are included in streaming chunks too:

```python
async for chunk in client.chat_stream(...):
    print(f"Chunk for conversation: {chunk['conversation_id']}")
    print(chunk['message']['content'])
```

Every chunk includes:
- `conversation_id`
- `user_id`
- `model`
- `message`
- `done`

## API Reference

### Request Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | No | User identifier (default: "anonymous") |
| `conversation_id` | string | No | Conversation identifier (auto-generated if missing) |

### Response Fields

| Field | Type | Always Present | Description |
|-------|------|----------------|-------------|
| `conversation_id` | string | Yes | Conversation ID (provided or generated) |
| `user_id` | string | Yes | User ID (provided or "anonymous") |

## Migration from No conversation_id

If you were not using `conversation_id`:

**Before:**
```python
response = await client.chat(
    model="ollama/llama2",
    messages=[...]
)
# Each request got a new auto-generated conversation_id
```

**After:**
```python
# Store conversation_id per channel/session
conversations = {}

channel_id = message.channel.id
if channel_id not in conversations:
    # First message - will auto-generate
    response = await client.chat(...)
    conversations[channel_id] = response['conversation_id']
else:
    # Continue existing conversation
    response = await client.chat(
        ...,
        options={'conversation_id': conversations[channel_id]}
    )
```

## Troubleshooting

### AI doesn't remember context

**Check:**
1. Are you using the same `conversation_id` for related messages?
2. Is `conversation_id` being passed correctly?

```python
# Log to verify
logging.debug(f"Using conversation_id: {conversation_id}")
```

### Context mixing between channels

**Check:**
1. Each channel should have unique `conversation_id`
2. Not reusing same `conversation_id` across channels

```python
# Wrong - same ID for all channels
conversation_id = "general"

# Right - unique ID per channel
conversation_id = f"discord_channel_{message.channel.id}"
```

### Generated IDs keep changing

**Problem:** Not saving the returned `conversation_id`.

```python
# Wrong - generates new ID each time
response = await client.chat(...)

# Right - save and reuse
if not conversation_id:
    response = await client.chat(...)
    conversation_id = response['conversation_id']
else:
    response = await client.chat(
        ...,
        options={'conversation_id': conversation_id}
    )
```

## Summary

**Key Points:**
- ✅ Use `conversation_id` to separate conversations
- ✅ API auto-generates if not provided
- ✅ Always returned in response (save it!)
- ✅ Combine with `user_id` for best results
- ✅ Use meaningful, unique IDs
- ✅ Works with streaming

**Format:**
```
{provider}_{scope}_{identifier}
```

**Examples:**
- `discord_channel_123456`
- `discord_thread_789012`
- `web_session_abc123`
- `slack_channel_general`

---

[← Back to API Reference](api.md) | [Documentation Home](../README.md)
