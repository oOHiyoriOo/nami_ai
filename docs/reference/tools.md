# Tools System

Nami AI supports function calling through local tools in `OllamaTools/` plus optional MCP tools. Local tool definitions are normal Python modules that export `get_tool()`. Files prefixed with `dream_` are excluded from normal chat loading and are used only by the DreamService.

## Tool Definition Shape

A tool module returns a dictionary like this:

```python
{
    "type": "function",
    "safe": True,              # optional; safe tools may run concurrently
    "categories": ["memory_read"],
    "function": {
        "name": "search_memory",
        "description": "Search the memory database",
        "parameters": {...}
    },
    "func": search_memory,
}
```

`tool_executor.py` validates arguments against the JSON schema, then executes the tool as:

```python
result = await tool_fn(**validated_args)
```

So no, there is no magical injected client object here. If a tool needs shared services, fetch them from `g_data` or `pipeline_ctx`.

---

## Built-in Tools

### search_memory

Search Nami's memory graph for relevant past interactions.

**Function:**
```python
async def search_memory(query: str, person: str = "") -> str
```

**Parameters:**
- `query` (string, required) - Search text
- `person` (string, optional) - Restrict results to a specific person by name or scoped ID like `discord:123`. Omit it (empty string in the current implementation) for a global search.

**Behavior:**
- If `person` contains `:`, it is treated as a scoped user ID directly
- Otherwise the tool resolves the name with a regex match against `(:Person {name})`
- Internally calls `memory_db.search(query, top_k=5, filter_user_id=...)`

**Returns:**
```json
{
  "success": true,
  "query": "hiking preferences",
  "data": [
    {"memory": "KnowledgeUnit({...})", "similarity": "0.87"}
  ]
}
```

**Example:**
```text
Tool Call: search_memory(query="coffee preferences", person="discord:123456789")
```

---

### mcp_playwright_browser_navigate / mcp_playwright_browser_snapshot

Browse and extract content from web pages using a real Chromium browser (via Playwright MCP). Handles JavaScript-heavy sites, cookies, and redirects. Anti-bot configured via `config/playwright-mcp.json`.

**Usage pattern:**
```text
1. mcp_playwright_browser_navigate(url="https://example.com/article")
2. mcp_playwright_browser_snapshot()  → returns accessible text of the page
```

Typical workflow: `search_web` to get candidate URLs → `mcp_playwright_browser_navigate` + `mcp_playwright_browser_snapshot` to read each page.

Additional available tools (prefixed `mcp_playwright_`): `browser_screenshot`, `browser_click`, `browser_type`, `browser_scroll`, `browser_go_back`, `browser_evaluate`, etc.

---

## Sandbox Tools

These tools operate on the optional sandbox environment and are active only when `sandbox.enabled: true`.

### run_bash

Execute a shell command in the sandbox.

**Function:**
```python
async def run_bash(command: str) -> str
```

**Parameters:**
- `command` (string, required) - Command to execute

**Returns:** full output for short commands, or a job descriptor if the command is backgrounded.

### get_job_output

Read output from a sandbox job.

**Function:**
```python
async def get_job_output(job_id: str) -> str
```

**Parameters:**
- `job_id` (string, required)

### list_jobs

List tracked sandbox jobs.

**Function:**
```python
async def list_jobs() -> str
```

### kill_job

Terminate a sandbox job.

**Function:**
```python
async def kill_job(job_id: str) -> str
```

**Parameters:**
- `job_id` (string, required)

### reset_sandbox

Reset sandbox state and clear tracked jobs.

**Function:**
```python
async def reset_sandbox() -> str
```

**Configuration:**
```yaml
sandbox:
  enabled: true
  host: sandbox
  port: 22
  username: root
  # password: resolved from env, /secrets, or config
  fg_timeout: 15.0
  max_output_kb: 16
```

---

## Person / Location Graph Tools

### create_person

Create or update a `(:Person)` node.

**Function:**
```python
async def create_person(name: str, description: str = "", relationship: str = "") -> str
```

**Parameters:**
- `name` (string, required) - Display name or handle
- `description` (string, optional) - Who this person is
- `relationship` (string, optional) - Relationship to the current user or to Nami

**Behavior:**
- Slugifies `name` to create a stable `person_id`
- Calls `memory_db.add_person(...)` using `MERGE`

**Returns:**
```json
{
  "success": true,
  "data": {
    "person_id": "sarah-connor",
    "name": "Sarah Connor",
    "action": "upserted"
  }
}
```

### create_location

Create or update a `(:Location)` node.

**Function:**
```python
async def create_location(name: str, description: str = "") -> str
```

**Parameters:**
- `name` (string, required) - Location name
- `description` (string, optional) - What the place is

**Behavior:**
- Slugifies `name` into `location_id`
- Calls `memory_db.add_location(...)` using `MERGE`

### remember_about_person

Store a third-party fact as a `KnowledgeUnit` linked to a person with `IS_ABOUT`.

**Function:**
```python
async def remember_about_person(person_name: str, fact: str) -> str
```

**Parameters:**
- `person_name` (string, required) - Existing person's name
- `fact` (string, required) - Fact to store

**Behavior:**
- Fuzzy-matches an existing `Person` node by name
- Creates a `KnowledgeUnit`
- Links it with `(knowledge)-[:IS_ABOUT]->(person)`

**Returns:**
```json
{
  "success": true,
  "data": {
    "fact_id": "uuid",
    "person_id": "sarah-connor",
    "person_name": "Sarah Connor",
    "fact": "prefers tea over coffee",
    "action": "stored"
  }
}
```

### link_my_identity

Link the current user's identity to another platform identity using `SAME_PERSON_AS`.

**Function:**
```python
async def link_my_identity(other_platform: str, other_id: str) -> str
```

**Parameters:**
- `other_platform` (string, required) - Platform name such as `discord` or `whatsapp`
- `other_id` (string, required) - Platform-specific ID or username

**Behavior:**
- Reads the current request's `user_id` from `pipeline_ctx`
- Builds a new scoped ID as `<other_platform>:<other_id>`
- Calls `memory_db.link_person_identities(current_user_id, other_user_id, linked_by="user_request")`

**Note:** the current implementation links existing `Person` nodes; it does not create missing identities automatically.

---

## Creating Custom Tools

### Basic Pattern

```python
from lib.global_registry import g_data
from OllamaTools import tool_success, tool_error


async def lookup_person_memories(person_id: str, query: str) -> str:
    db = g_data.get("memory_db")
    if not db:
        return tool_error("Memory database not available", person_id=person_id)

    results = await db.search(query=query, filter_user_id=person_id, top_k=3)
    payload = [
        {"memory": str(memory), "score": score}
        for memory, score in results
    ]
    return tool_success(payload, person_id=person_id, query=query)


def get_tool() -> dict:
    return {
        "type": "function",
        "safe": True,
        "categories": ["memory_read"],
        "function": {
            "name": "lookup_person_memories",
            "description": "Search recent memories for one scoped user ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "person_id": {
                        "type": "string",
                        "description": "Scoped user ID like discord:123"
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query"
                    }
                },
                "required": ["person_id", "query"]
            }
        },
        "func": lookup_person_memories,
    }
```

### Accessing Pipeline Context

If a tool needs the caller's current identity or conversation, use `pipeline_ctx`:

```python
from lib.services.ai_pipeline import pipeline_ctx

ctx = pipeline_ctx.get()
current_user_id = ctx.get("user_id")
conversation_id = ctx.get("conversation_id")
```

### Loading Rules

`ToolLoader` scans `OllamaTools/*.py`, skips `__init__.py`, and ignores files starting with `dream_`.

### Return Format Helpers

Use the helpers from `OllamaTools/__init__.py`:

```python
from OllamaTools import tool_success, tool_error

return tool_success({"result": 42}, query="example")
return tool_error("Something went wrong", query="example")
```

They produce standardized JSON strings like:

```json
{"success": true, "data": {"result": 42}, "query": "example"}
```

---

## Best Practices

1. **Describe tools clearly** - Give the model enough detail to use them correctly
2. **Validate inputs** - Fail fast with `tool_error(...)`
3. **Use `g_data` for shared services** - `memory_db`, config, adapter manager, sandbox, etc.
4. **Use `pipeline_ctx` for caller identity** - especially for user-aware tools
5. **Mark read-only tools as `safe`** - they can execute concurrently
6. **Avoid leaking secrets** - never return raw config or credentials
7. **Prefer async I/O** - blocking tools stall the whole request path, which is apparently rude

---

## Debugging Tools

### List Loaded Tools

```python
from lib.utils.dynamic_loader import load_tools

tools = await load_tools()
for tool in tools:
    print(tool["function"]["name"])
```

### Inspect Tool Activity in Logs

```bash
tail -f logs/*.log | grep -i tool
```

### Disable a Tool

Rename the module so the loader no longer sees it:

```bash
mv OllamaTools/my_tool.py OllamaTools/my_tool.py.disabled
```

---

## Tool Limits

Configure maximum tool calls per provider:

```yaml
providers:
  ollama:
    max_tool_calls: 3
```

---

## See Also

- [API Reference](api.md)
- [Memory System](../memory/overview.md)
- [Architecture](../ARCHITECTURE.md)
- Repository: https://github.com/oOHiyoriOo/nami_ai
