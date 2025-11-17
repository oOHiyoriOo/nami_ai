# Nami AI - Personality Proxy API

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)

Nami AI is a personality proxy system that exposes AI personalities with Neo4j-backed memory through an OpenAPI interface. This allows you to use consistent AI personalities and memories with any AI backend.

**NEW**: This project has been refactored from a Discord bot to a personality proxy API. The Discord bot code is preserved in the git history.

## Features

*   **OpenAPI Interface:** RESTful API for easy integration with any AI system
*   **Personality Management:** System prompts that define AI character and behavior
*   **Memory System:** Neo4j graph database for long-term memory (episodic, knowledge, procedural)
*   **Conversation History:** SQLite-based conversation tracking
*   **Tool Integration:** Built-in tools for web search, memory queries, and more
*   **Privacy-Focused:** Runs locally, ensuring your data stays on your machine
*   **Extensible:** Designed with modularity in mind

## Architecture

```
Client (Any AI) → OpenAPI Interface → Personality Proxy
                                     ├── Neo4j (Memory DB)
                                     ├── Ollama (LLM)
                                     ├── SQLite (History)
                                     └── Tools System
```

## Installation

1.  **Clone the repository:**
    ```bash
    git clone git@github.com:oOHiyoriOo/nami_ai.git
    cd nami_ai
    ```

2.  **Create and activate a virtual environment (recommended):**
    ```bash
    conda create -n nami python=3.12
    
    conda activate nami
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

## Configuration

Edit `config.yml` to configure the personality proxy:

```yaml
api:
  host: "0.0.0.0"
  port: 8000

neo4j:
  uri: "bolt://localhost:7687"
  user: "neo4j"
  pass: "your_password"

ollama:
  url: "http://localhost:11434"
  model: "qwen2.5:32b-instruct"
  system_prompt: "nami"  # Name of the file in system_prompt/ directory
  max_tool_calls: 10

memory_db:
  model: "all-MiniLM-L6-v2"  # Embedding model

bot:
  log_level: "INFO"
```

Key settings:
*   `api`: API server configuration (host, port)
*   `neo4j`: Connection details for the Neo4j graph database used for memory
*   `ollama`: Configuration for the Ollama LLM service
*   `memory_db`: Embedding model configuration
*   `bot.log_level`: Sets the logging level (e.g., INFO, DEBUG)

## Project Structure

```
nami_ai/
├── api/
│   ├── models.py              # Pydantic models for API
│   └── conversation_service.py # Core chat logic
├── api_server.py              # FastAPI application (main entry point)
├── lib/
│   ├── configurationFile.py   # Config loading
│   ├── memory_db.py           # Neo4j memory interface
│   ├── sqlite_helper.py       # SQLite history
│   ├── ollama_helper.py       # Ollama LLM integration
│   ├── vector_helper.py       # Vector/embedding utilities
│   ├── system_prompt_parser.py # System prompt loader
│   └── ...
├── system_prompt/             # Personality definitions (Markdown files)
│   ├── nami.md
│   └── ranni.md
├── OllamaTools/              # Tool implementations
├── main_discord_bot.py       # Legacy Discord bot (preserved)
├── config.yml                # Configuration
├── requirements.txt          # Python dependencies
└── README.md                 # This file
```

Key components:
*   **`api_server.py`:** The main entry point for the API. Initializes FastAPI, loads configuration, sets up databases, and exposes REST endpoints.
*   **`api/conversation_service.py`:** Core conversation logic extracted from the Discord bot, adapted for API use.
*   **`api/models.py`:** Pydantic models for API requests and responses.
*   **`lib/memory_db.py`:** Neo4j graph database interface for long-term memory.
*   **`lib/sqlite_helper.py`:** SQLite interface for conversation history.
*   **`system_prompt/`:** Directory containing Markdown files that define different AI personas.
*   **`OllamaTools/`:** Directory containing tool definitions that the AI can use.

## Usage

### Running the API Server

Ensure your `config.yml` is correctly configured and all dependencies (Neo4j, Ollama) are running.

```bash
# Activate your environment (if using one)
# conda activate nami

# Run the API server
python api_server.py
```

The API will be available at `http://localhost:8000` (or the host/port configured in `config.yml`).

### API Documentation

Once running, access the interactive API documentation:
- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`
- **OpenAPI JSON:** `http://localhost:8000/openapi.json`

### Example API Usage

**Python:**
```python
import requests

response = requests.post(
    "http://localhost:8000/chat",
    json={
        "message": "Hello, how are you?",
        "user_id": "user123",
        "conversation_id": "my_conversation"
    }
)

print(response.json()["response"])
```

**cURL:**
```bash
curl -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello!", "user_id": "user123", "conversation_id": "test"}'
```

### Main Endpoints

- `POST /chat` - Send a message and get a response
- `GET /conversations/{id}/history` - Get conversation history
- `POST /memories/search` - Search memories
- `POST /memories` - Create a new memory
- `GET /health` - Health check
- `GET /personality` - Get personality info
  
## Logs

Important logs generated by the bot can be found in the `logs/` directory. These logs include:

* **Startup logs:** Information about bot initialization, configuration loading, and environment setup.
* **Command logs:** Details of commands received and executed, including errors and results.
* **Event logs:** Records of Discord events such as messages, reactions, and member updates.
* **AI response logs:** Logs of AI-generated responses, including semantic search results and tool usage.
* **Error logs:** Tracebacks and error messages for debugging.
* **Database logs:** Interactions with Neo4j and FAISS, including connection status and query results.
* **Tool usage logs:** When the bot invokes tools (e.g., web search, memory search), these actions are logged for audit and debugging.

## Workflow: How a Chat Request is Processed

1. **API Request Received:** Client sends POST request to `/chat` endpoint
2. **Message Storage:** User message is stored in SQLite conversation history
3. **History Retrieval:** Recent conversation history is loaded (up to `max_history` messages)
4. **Memory Search:** Neo4j is queried for relevant memories based on message content
5. **Context Building:** System prompt, user context, memories, and history are combined
6. **LLM Processing:** Complete context is sent to Ollama with available tools
7. **Tool Execution:** If LLM requests tools (e.g., web search), they are executed and results added to context
8. **Response Generation:** Final response is generated by LLM
9. **Memory Storage:** Important information is extracted and stored in Neo4j
10. **Response Sent:** API returns response, thinking process (if requested), and tools used
11. **Logging:** All steps, including errors and tool usage, are logged for debugging

## Memory System

The Neo4j-based memory system supports three types of memories:

### Episodic Memory
Experiences and events with emotional context.

### Knowledge Units
Factual information and statements.

### Procedural Units
Skills, processes, and how-to information.

Memories are automatically extracted during conversations and can also be created via the API.


