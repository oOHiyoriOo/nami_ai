# Tools System

The Personality Proxy supports function calling (tools), allowing AI personalities to perform actions like web searches, memory queries, and custom operations.

## Built-in Tools

### search_memory

Search the Neo4j memory database.

**Function:**
```python
async def search_memory(client, source_user, query: str, limit: int = 10)
```

**Parameters:**
- `query` (string) - Search query
- `limit` (int) - Max results (default: 10)

**Returns:**
```python
[
    {"type": "EpisodicMemory", "content": "...", "score": 0.85},
    {"type": "KnowledgeUnit", "content": "...", "score": 0.78}
]
```

**Example Usage:**
```
AI: Let me search my memories about that...
Tool Call: search_memory(query="hiking preferences", limit=5)
Result: Found memories about user enjoying mountain hiking
AI: I remember you mentioned you love hiking in the mountains!
```

---

### search_web

Search the web using Brave Search API.

**Function:**
```python
async def search_web(client, source_user, query: str, count: int = 5)
```

**Parameters:**
- `query` (string) - Search query
- `count` (int) - Number of results (default: 5)

**Returns:**
```python
[
    {"title": "...", "url": "...", "description": "..."},
    ...
]
```

**Configuration:**
```yaml
bot:
  brave_search_token: YOUR_BRAVE_API_KEY
```

Get API key: https://brave.com/search/api/

**Example Usage:**
```
User: What's the weather in Paris?
AI: Let me search for current weather...
Tool Call: search_web(query="Paris weather today", count=3)
Result: Weather data from search results
AI: It's currently 15°C and partly cloudy in Paris.
```

---

### visit_web_page

Extract content from a web page.

**Function:**
```python
async def visit_web_page(client, source_user, url: str)
```

**Parameters:**
- `url` (string) - URL to fetch

**Returns:**
```python
{
    "title": "Page Title",
    "content": "Extracted text content...",
    "url": "https://example.com"
}
```

**Example Usage:**
```
User: What does this article say? https://example.com/article
AI: Let me read that article...
Tool Call: visit_web_page(url="https://example.com/article")
Result: Article content extracted
AI: The article discusses...
```

---

### generate_comfy_image

Generate images using ComfyUI.

**Function:**
```python
async def generate_comfy_image(client, source_user, prompt: str, negative_prompt: str = "")
```

**Parameters:**
- `prompt` (string) - Image generation prompt
- `negative_prompt` (string) - Things to avoid

**Returns:**
```python
{
    "status": "success",
    "image_paths": ["path/to/image.png"]
}
```

**Configuration:**
```yaml
comfyui:
  server: localhost:8188
  workflow: workflow.json
  output: image_output
  positive_node: '6'
  negative_node: '7'
  sampler_node: '3'
```

**Requires:**
- ComfyUI running locally
- Workflow JSON file configured

---

### query_audit_log

Query system logs (Discord-specific, may need adaptation).

---

### query_discord_user

Query Discord user information (legacy, Discord-specific).

---

## Creating Custom Tools

### 1. Basic Tool Structure

Create a file in `OllamaTools/my_tool.py`:

```python
"""
My Custom Tool
"""
async def my_custom_tool(client, source_user, param1: str, param2: int = 10):
    """
    Tool description that the AI will see.

    Args:
        client: API client (may be None)
        source_user: User who triggered this
        param1: First parameter description
        param2: Second parameter with default

    Returns:
        Result data (string, dict, or list)
    """
    # Your implementation here
    result = f"Processed {param1} with value {param2}"

    # Can return string, dict, or list
    return result


# Tool definition for function calling
tool_definition = {
    "type": "function",
    "function": {
        "name": "my_custom_tool",
        "description": "One-line description of what this tool does",
        "parameters": {
            "type": "object",
            "properties": {
                "param1": {
                    "type": "string",
                    "description": "Description of param1"
                },
                "param2": {
                    "type": "integer",
                    "description": "Description of param2",
                    "default": 10
                }
            },
            "required": ["param1"]
        }
    },
    "func": my_custom_tool  # Reference to function
}
```

### 2. Tool Loading

Tools are automatically loaded from `OllamaTools/`:

```python
# In lib/tool_loader.py
async def load_tools(client):
    tools = []

    for file in glob.glob("OllamaTools/*.py"):
        module = import_module(file)
        if hasattr(module, 'tool_definition'):
            tools.append(module.tool_definition)

    return tools
```

### 3. Tool Parameters

**Supported types:**
- `string` - Text
- `integer` - Numbers
- `number` - Floats
- `boolean` - True/False
- `array` - Lists
- `object` - Dictionaries

**Example:**
```python
"parameters": {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "User's name"
        },
        "age": {
            "type": "integer",
            "description": "User's age",
            "minimum": 0,
            "maximum": 150
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of tags"
        },
        "options": {
            "type": "object",
            "description": "Additional options",
            "properties": {
                "verbose": {"type": "boolean"}
            }
        }
    },
    "required": ["name"]
}
```

### 4. Return Values

Tools can return:

**String:**
```python
return "Task completed successfully"
```

**Dictionary:**
```python
return {
    "status": "success",
    "data": {"result": 42},
    "message": "Processed successfully"
}
```

**List:**
```python
return [
    {"id": 1, "name": "Item 1"},
    {"id": 2, "name": "Item 2"}
]
```

The AI will receive the return value as context for its response.

## Example Tools

### Weather Tool

```python
"""
Weather lookup tool
"""
import aiohttp

async def get_weather(client, source_user, city: str):
    """
    Get current weather for a city.

    Args:
        city: City name

    Returns:
        Weather information
    """
    async with aiohttp.ClientSession() as session:
        url = f"https://wttr.in/{city}?format=j1"
        async with session.get(url) as resp:
            data = await resp.json()

    current = data['current_condition'][0]

    return {
        "city": city,
        "temperature": f"{current['temp_C']}°C",
        "condition": current['weatherDesc'][0]['value'],
        "humidity": f"{current['humidity']}%",
        "wind": f"{current['windspeedKmph']} km/h"
    }


tool_definition = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name (e.g., 'London', 'New York')"
                }
            },
            "required": ["city"]
        }
    },
    "func": get_weather
}
```

### Calculator Tool

```python
"""
Calculator tool
"""
async def calculate(client, source_user, expression: str):
    """
    Evaluate a mathematical expression.

    Args:
        expression: Math expression (e.g., "2 + 2", "sqrt(16)")

    Returns:
        Calculation result
    """
    import math

    # Safe evaluation (restricted namespace)
    allowed_names = {
        'abs': abs,
        'round': round,
        'min': min,
        'max': max,
        'sum': sum,
        'pow': pow,
        'sqrt': math.sqrt,
        'sin': math.sin,
        'cos': math.cos,
        'tan': math.tan,
        'pi': math.pi,
        'e': math.e
    }

    try:
        result = eval(expression, {"__builtins__": {}}, allowed_names)
        return {
            "expression": expression,
            "result": result
        }
    except Exception as e:
        return {
            "error": str(e),
            "expression": expression
        }


tool_definition = {
    "type": "function",
    "function": {
        "name": "calculate",
        "description": "Evaluate a mathematical expression",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Math expression (e.g., '2+2', 'sqrt(16)', 'sin(pi/2)')"
                }
            },
            "required": ["expression"]
        }
    },
    "func": calculate
}
```

### Database Query Tool

```python
"""
Database query tool
"""
import aiosqlite

async def query_database(client, source_user, query: str):
    """
    Execute a read-only database query.

    Args:
        query: SQL SELECT query

    Returns:
        Query results
    """
    # Validate query is read-only
    if not query.strip().upper().startswith('SELECT'):
        return {"error": "Only SELECT queries allowed"}

    async with aiosqlite.connect('database.db') as db:
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]

    return {
        "columns": columns,
        "rows": rows,
        "count": len(rows)
    }


tool_definition = {
    "type": "function",
    "function": {
        "name": "query_database",
        "description": "Execute a read-only SQL query",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "SQL SELECT query"
                }
            },
            "required": ["query"]
        }
    },
    "func": query_database
}
```

## Tool Best Practices

### 1. Clear Descriptions

```python
# Good
"description": "Get current weather for a city using wttr.in API"

# Bad
"description": "Weather"
```

### 2. Detailed Parameters

```python
# Good
"city": {
    "type": "string",
    "description": "City name (e.g., 'London', 'Tokyo', 'New York')"
}

# Bad
"city": {
    "type": "string"
}
```

### 3. Error Handling

```python
async def my_tool(client, source_user, param):
    try:
        result = await some_operation(param)
        return {"status": "success", "data": result}
    except Exception as e:
        logging.error(f"Tool error: {e}")
        return {"status": "error", "message": str(e)}
```

### 4. Async Operations

```python
# Good - async/await
async def my_tool(client, source_user, url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.json()

# Avoid - blocking
def my_tool(client, source_user, url):
    import requests
    return requests.get(url).json()  # Blocks event loop!
```

### 5. Type Validation

```python
async def my_tool(client, source_user, count: int):
    # Validate
    if not isinstance(count, int):
        return {"error": "count must be an integer"}

    if count < 1 or count > 100:
        return {"error": "count must be between 1 and 100"}

    # Process...
```

### 6. Security

```python
# Never execute arbitrary code
# BAD:
async def execute_code(client, source_user, code):
    return exec(code)  # DANGEROUS!

# Never expose sensitive data
# BAD:
async def get_config(client, source_user):
    return config  # May contain API keys!

# Validate inputs
async def safe_tool(client, source_user, url):
    # Check URL is safe
    if not url.startswith(('http://', 'https://')):
        return {"error": "Invalid URL"}

    # Rate limiting
    # Authentication
    # Input sanitization
```

## Debugging Tools

### Check Loaded Tools

```python
from lib.tool_loader import load_tools

tools = await load_tools(None)
print(f"Loaded {len(tools)} tools:")
for tool in tools:
    print(f"  - {tool['function']['name']}")
```

### Test Tool Directly

```python
from OllamaTools.my_tool import my_custom_tool

result = await my_custom_tool(
    client=None,
    source_user=None,
    param1="test"
)
print(result)
```

### View Tool Calls in Logs

```bash
tail -f logs/*.log | grep -i "tool"
```

## Disabling Tools

### Per Request

```json
{
  "model": "llama2",
  "messages": [...],
  "tools": null  // No tools
}
```

### Per Tool

Rename file to disable:
```bash
mv OllamaTools/my_tool.py OllamaTools/my_tool.py.disabled
```

### Globally

In `api_server.py`:
```python
# Comment out tools loading
# tools = request.tools
tools = None  # Disable all tools
```

## Tool Limits

Configure max tool calls in `config.yml`:

```yaml
providers:
  ollama:
    max_tool_calls: 3  # Prevent infinite loops
```

## See Also

- [API Reference](api.md) - API documentation
- [Quick Start](../guides/quickstart.md) - Get started
- [Memory System](../memory/overview.md) - Memory tools

---

Need help? [Open an issue](https://github.com/oOHiyoriOo/nami_ai/issues) on GitHub.
