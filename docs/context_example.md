# What the AI Sees — Context & Chat History Example

This document shows the exact message list sent to the AI on every request.
Understanding this helps tune personality, memory, and history behaviour.

---

## Message Order (always)

```
1. [system]     Personality prompt                (nami.md / ranni.md)
2. [tool]       user_info JSON context            (role=tool, name=user_info)
3. [system]     Relevant memories                 (if any match the query)
4. [system]     Completed sandbox jobs            (optional, if any finished since last turn)
5. [system]     Completed background tasks        (optional, research / scheduler / dream)
── conversation history ──────────────────────────────────────────────
6. [user]       Oldest message in window
7. [assistant]  ...
8. [user]       ...
...
N. [user]       Current message (the one being answered)
```

System and tool context messages are prepended before history. Assistant turns stay plain text; user metadata lives in the dedicated `user_info` tool message instead of being squeezed into a fake system sentence, because apparently we can afford one good idea.

---

## Concrete Example (Discord, 3-message window)

### System message 1 — Personality
```
You are Nami, a highly efficient, analytical, and resourceful lab assistant to <username>.
Your primary function is to assist <username> with research, data analysis, information
retrieval, and task execution.
...
[full contents of system_prompt/nami.md with {{date}} / {{time}} replaced]
```

### Message 2 — `user_info` tool context

When `enable_personality=true` and `user_id` is present, `ContextBuilder` injects a `role=tool`, `name=user_info` message immediately after the system prompt.

**Discord example:**
```json
{
  "user": "<username>",
  "username": "<username>",
  "user_id": "discord:123456789012345678",
  "platform": "Discord",
  "channel": "lab-chat",
  "guild": "Example Lab",
  "is_dm": false
}
```

**WhatsApp example:**
```json
{
  "user": "<username>",
  "username": "+15551234567",
  "user_id": "whatsapp:+15551234567",
  "platform": "Whatsapp",
  "channel": null,
  "guild": null,
  "is_dm": true
}
```

**REST API (anonymous):** if no `user_id` is provided, the `user_info` message is omitted.

### System message 3 — Relevant memories (top_k=5, threshold=0.65)
```
Relevant memories:
- <username> prefers concise answers with no filler phrases. (Score: 0.91)
- <username> is working on a Neo4j-backed memory system for Nami. (Score: 0.78)
- <username> uses their preferred Python environment. (Context)
```
*(Omitted entirely when no memories score above the threshold)*

### System message 4 — Completed sandbox job (optional)
```
[Sandbox] Background job(s) completed since your last message:
  job_id=a3f9 | exit_code=0 | elapsed=47.2s
  command: python3 train.py --epochs 10
  output:
Epoch 1/10: loss=0.842
Epoch 2/10: loss=0.731
...
```

---

## Conversation History

User messages are formatted by `format_user_message()`:

```
{display_name} [{YYYY-MM-DD HH:MM:SS}] : {content}
```

Assistant messages are plain content with no prefix.

### Example window (limit=20 messages fetched, oldest → newest):

```
[user]      <username> [2026-03-27 14:00:01] : What are you working on?
[assistant] Right now I'm helping you set up the memory pipeline. What do you need?
[user]      <username> [2026-03-27 14:01:33] : Can you run the test suite?
[assistant] Sure, running it now.
[user]      <username> [2026-03-27 14:02:10] : How did it go?   ← current message
```

### Reply with context (when user replies to an earlier message):
```
[user]      <username> [2026-03-27 14:05:00] : [Replying to conversation]
            <username>: What are you working on?
            Nami: Right now I'm helping you set up the memory pipeline.
            [Reply message]
            Make that the priority.
```

---

## WhatsApp / REST API path

The bridge sends the full history itself (stored in its own SQLite DB).
Messages arrive as a plain `messages` array in the Ollama-compatible request body:

```json
{
  "model": "ollama/qwen3:32b",
  "user_id": "whatsapp:+15551234567",
  "conversation_id": "whatsapp:+15551234567",
  "enable_memory": true,
  "enable_personality": true,
  "messages": [
    { "role": "user",      "content": "Hey Nami!" },
    { "role": "assistant", "content": "Hey! What's up?" },
    { "role": "user",      "content": "Run a quick benchmark for me" }
  ]
}
```

These are passed directly to `context_builder.build_context()` — the same
system messages (personality, context, memories) are prepended exactly as
in the Discord path. The only difference is that user content has no
`display_name [timestamp] :` prefix (bridge sends raw content).

---

## Key limits & config

| Setting | Default | Where |
|---|---|---|
| Discord bridge history window | 50 messages by default | `adapters/discord_bridge/index.js` → `new ConversationHistory('history.db', 50)` |
| Memory results injected | top 5 | `context_builder.build_context()` → `memory_service.retrieve_relevant_memories(..., top_k=5, context_k=20)` |
| Memory similarity threshold | 0.65 | `config.yml → memory.similarity_threshold` |
| Context pool (candidate set) | 20 | `context_builder.build_context()` → `context_k=20` |
| Max tool calls per repeated round | 3 by default | `config.yml → bot.max_tool_calls` |

---

## Things worth considering

- **User messages can carry timestamps and display names** when a bridge stores formatted history; assistant turns remain plain text.
- **WhatsApp identities are scoped IDs** such as `whatsapp:+15551234567`, while the `user_info` payload carries the normalized platform metadata.
- **Discord history is stored locally** in SQLite inside the bridge container — no Discord API polling loop just to rebuild context.
- **Memories are searched using only the last user message** as the fresh retrieval query, then merged with a sliding window of prior recalled memories for the same conversation.
- **No `user_id` = no memory.** When `user_id` is omitted, Nami skips both memory retrieval and memory writing for that caller.
