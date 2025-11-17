"""
Personality Proxy API Server - Ollama-compatible API
Mimics the Ollama API format while adding personality and memory features.
"""
import logging
import os
import time
import json
import asyncio
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any
from datetime import datetime

from fastapi import FastAPI, HTTPException, status, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from colorama import Fore, init

from lib.memory_db import MemoryDb
from lib.tool_loader import load_tools
from lib.global_registry import g_data
from lib.asyncsqlite import AsyncSQLite
from lib.configurationFile import ConfigurationFile
from lib.system_prompt_parser import NamiSystemPrompt
from lib.ai_providers import ProviderRegistry, Message

init(convert=True, autoreset=True)

# --- Logging Configuration ---
os.makedirs('./logs', exist_ok=True)

cfg_temp = ConfigurationFile("config.yml")
log_level_str = cfg_temp.data.get('bot', {}).get('log_level', 'INFO')
log_level = getattr(logging, str(log_level_str).upper(), logging.INFO)

logging.basicConfig(
    level=log_level,
    format=(f'[%(asctime)s] {Fore.YELLOW} {"[%(levelname)s]":<8} {Fore.RESET} [%(name)s] %(message)s'),
    handlers=[
        logging.FileHandler(f"./logs/api_{time.strftime('%Y-%m-%d_%H_%M_%S')}.log", 'w', 'utf-8'),
        logging.StreamHandler()
    ],
    force=True
)
logging.info(f"Logging configured with level {logging.getLevelName(log_level)}.")


# --- Pydantic Models (Ollama-compatible) ---

class OllamaMessage(BaseModel):
    """Message format compatible with Ollama API."""
    role: str
    content: str
    images: Optional[List[str]] = None
    tool_calls: Optional[List[Dict]] = None


class OllamaChatRequest(BaseModel):
    """Chat request compatible with Ollama API."""
    model: str
    messages: List[OllamaMessage]
    stream: bool = False
    tools: Optional[List[Dict]] = None
    format: Optional[str] = None
    options: Optional[Dict[str, Any]] = None
    # Personality proxy extensions
    user_id: Optional[str] = Field(None, description="User ID for memory/personalization")
    conversation_id: Optional[str] = Field(None, description="Conversation ID for context")
    enable_memory: bool = Field(True, description="Enable memory system")
    enable_personality: bool = Field(True, description="Enable personality prompt")


class OllamaChatResponse(BaseModel):
    """Chat response compatible with Ollama API."""
    model: str
    created_at: str
    message: OllamaMessage
    done: bool
    total_duration: Optional[int] = None
    load_duration: Optional[int] = None
    prompt_eval_count: Optional[int] = None
    eval_count: Optional[int] = None


class OllamaGenerateRequest(BaseModel):
    """Generate request compatible with Ollama API."""
    model: str
    prompt: str
    stream: bool = False
    options: Optional[Dict[str, Any]] = None
    system: Optional[str] = None
    template: Optional[str] = None
    context: Optional[List[int]] = None
    # Personality proxy extensions
    user_id: Optional[str] = None
    enable_memory: bool = True
    enable_personality: bool = True


class OllamaTagsResponse(BaseModel):
    """Response for listing models."""
    models: List[Dict[str, Any]]


# --- Application Lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    logging.info("Initializing Personality Proxy API (Ollama-compatible)...")

    # Load configuration
    cfg = g_data.get_or_create("cfg", ConfigurationFile, "config.yml")

    # Get provider configuration
    provider_name = cfg.data.get('ai_provider', 'ollama')
    provider_config = cfg.data.get('providers', {}).get(provider_name, cfg.data.get('ollama', {}))

    # Initialize AI provider
    try:
        provider = ProviderRegistry.get_provider(provider_name, provider_config)
        g_data.get_or_create("ai_provider", lambda: provider)
        g_data.get_or_create("ai_provider_name", lambda: provider_name)
        logging.info(f"Initialized AI provider: {provider_name}")
    except Exception as e:
        logging.error(f"Failed to initialize AI provider '{provider_name}': {e}")
        raise

    # Load system prompt
    system_prompt_filename = provider_config.get('system_prompt', 'nami')
    sys_prompt_instance = g_data.get_or_create(
        "system_prompt",
        NamiSystemPrompt,
        f"system_prompt/{system_prompt_filename}.md"
    )

    # Initialize memory database
    memory_db_instance = g_data.get_or_create(
        "memory_db",
        MemoryDb,
        neo4j_uri=cfg.data['neo4j']['uri'],
        neo4j_user=cfg.data['neo4j']['user'],
        neo4j_pass=cfg.data['neo4j']['pass'],
        model_name=cfg.data.get('memory_db', {}).get('model', 'all-MiniLM-L6-v2')
    )

    # Initialize history database
    with open('lib/Storage/history_schem.json') as schema_file:
        history_db = g_data.get_or_create(
            "history_db",
            AsyncSQLite,
            db_path="history.db",
            schema=json.load(schema_file)
        )

    await history_db.initialize()
    logging.info("History database initialized.")

    # Load tools
    loaded_tools = await load_tools(None)
    g_data.get_or_create("tools", lambda: loaded_tools)
    logging.info(f"Loaded {len(loaded_tools)} tools globally.")

    logging.info("="*70)
    logging.info(f"{Fore.GREEN}Personality Proxy API initialized!")
    logging.info(f"Provider: {provider_name}")
    logging.info(f"Personality: {system_prompt_filename}")
    logging.info(f"Model: {provider_config.get('model', 'default')}")
    logging.info("="*70)

    yield

    # Cleanup
    logging.info("Shutting down Personality Proxy API...")
    if memory_db_instance:
        memory_db_instance.close()
    logging.info("Shutdown complete.")


# --- FastAPI App ---
app = FastAPI(
    title="Personality Proxy API",
    description="Ollama-compatible API with personality and memory features",
    version="2.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Helper Functions ---

async def enhance_messages_with_personality(
    messages: List[OllamaMessage],
    user_id: str,
    conversation_id: str,
    enable_memory: bool,
    enable_personality: bool
) -> List[OllamaMessage]:
    """
    Enhance messages with personality prompt and memory.

    Args:
        messages: Original messages
        user_id: User identifier
        conversation_id: Conversation identifier
        enable_memory: Whether to include memories
        enable_personality: Whether to include personality prompt

    Returns:
        Enhanced messages list
    """
    enhanced = []

    # Add system prompt if personality is enabled
    if enable_personality:
        sys_prompt = await g_data.get("system_prompt").get_prompt()
        enhanced.append(OllamaMessage(role="system", content=sys_prompt))

    # Add user context
    if user_id:
        user_context = f"Context: You are talking to user ID '{user_id}'"
        enhanced.append(OllamaMessage(role="system", content=user_context))

    # Add memories if enabled
    if enable_memory and user_id:
        try:
            memory_db = g_data.get('memory_db')
            # Get last user message for memory search
            user_messages = [msg for msg in messages if msg.role == "user"]
            if user_messages and memory_db.get_total_entries() > 0:
                last_user_msg = user_messages[-1].content

                retrieved_memories = memory_db.search_with_context(
                    query=last_user_msg,
                    top_k=5,
                    context_k=20
                )

                if retrieved_memories:
                    similarity_threshold = 0.65
                    formatted_memories = []

                    for mem in retrieved_memories:
                        mem_text = mem.get('text')
                        mem_type = mem.get('type')
                        mem_score = mem.get('score', 0.0)

                        if not mem_text:
                            continue

                        if mem_type == 'context' or (mem_type == 'vector' and mem_score >= similarity_threshold):
                            score_info = f"(Score: {mem_score:.2f})" if mem_type == 'vector' else "(Context)"
                            formatted_memories.append(f"- {mem_text} {score_info}")

                    if formatted_memories:
                        memory_context = "Relevant memories:\n" + "\n".join(formatted_memories)
                        enhanced.append(OllamaMessage(role="system", content=memory_context))
                        logging.info(f"Added {len(formatted_memories)} memories to context")

        except Exception as e:
            logging.error(f"Error retrieving memories: {e}", exc_info=True)

    # Add original messages
    enhanced.extend(messages)

    return enhanced


# --- Ollama-Compatible Endpoints ---

@app.post("/api/chat")
@app.post("/v1/chat/completions")  # Also support OpenAI format
async def chat(request: OllamaChatRequest):
    """
    Chat completion endpoint (Ollama-compatible).
    Supports both Ollama and OpenAI formats.
    """
    provider = g_data.get("ai_provider")
    if not provider:
        raise HTTPException(status_code=503, detail="AI provider not initialized")

    try:
        start_time = time.time()

        # Get user/conversation IDs
        user_id = request.user_id or "anonymous"
        conversation_id = request.conversation_id or f"user_{user_id}"

        # Enhance messages with personality and memory
        enhanced_messages = await enhance_messages_with_personality(
            request.messages,
            user_id,
            conversation_id,
            request.enable_memory,
            request.enable_personality
        )

        # Convert to provider format
        provider_messages = [
            Message(
                role=msg.role,
                content=msg.content,
                tool_calls=msg.tool_calls
            )
            for msg in enhanced_messages
        ]

        # Get tools
        tools = request.tools
        if tools is None and request.enable_memory:
            # Use default tools
            loaded_tools = g_data.get("tools")
            if loaded_tools:
                tools = [{k: v for k, v in tool.items() if k != 'func'} for tool in loaded_tools]

        # Call AI provider
        if request.stream:
            async def generate_stream():
                async for chunk in provider.chat_stream(provider_messages, tools, model=request.model):
                    response_chunk = {
                        "model": request.model,
                        "created_at": datetime.utcnow().isoformat() + "Z",
                        "message": {
                            "role": "assistant",
                            "content": chunk
                        },
                        "done": False
                    }
                    yield json.dumps(response_chunk) + "\n"

                # Final chunk
                final_chunk = {
                    "model": request.model,
                    "created_at": datetime.utcnow().isoformat() + "Z",
                    "message": {
                        "role": "assistant",
                        "content": ""
                    },
                    "done": True
                }
                yield json.dumps(final_chunk) + "\n"

            return StreamingResponse(generate_stream(), media_type="application/x-ndjson")

        else:
            # Non-streaming response
            response = await provider.chat(provider_messages, tools, model=request.model)

            duration = int((time.time() - start_time) * 1000000000)  # nanoseconds

            return OllamaChatResponse(
                model=request.model,
                created_at=datetime.utcnow().isoformat() + "Z",
                message=OllamaMessage(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls
                ),
                done=True,
                total_duration=duration
            )

    except Exception as e:
        logging.error(f"Chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/generate")
async def generate(request: OllamaGenerateRequest):
    """
    Generate completion endpoint (Ollama-compatible).
    """
    # Convert generate request to chat format
    messages = []

    if request.system:
        messages.append(OllamaMessage(role="system", content=request.system))
    elif request.enable_personality:
        sys_prompt = await g_data.get("system_prompt").get_prompt()
        messages.append(OllamaMessage(role="system", content=sys_prompt))

    messages.append(OllamaMessage(role="user", content=request.prompt))

    # Create chat request
    chat_request = OllamaChatRequest(
        model=request.model,
        messages=messages,
        stream=request.stream,
        user_id=request.user_id,
        enable_memory=request.enable_memory,
        enable_personality=False  # Already added if needed
    )

    return await chat(chat_request)


@app.get("/api/tags")
async def list_tags():
    """List available models (Ollama-compatible)."""
    provider = g_data.get("ai_provider")
    if not provider:
        raise HTTPException(status_code=503, detail="AI provider not initialized")

    try:
        models = provider.list_models()
        return OllamaTagsResponse(
            models=[
                {
                    "name": model,
                    "modified_at": datetime.utcnow().isoformat() + "Z",
                    "size": 0,
                    "digest": "",
                }
                for model in models
            ]
        )
    except Exception as e:
        logging.error(f"List models error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/version")
@app.get("/")
async def version():
    """Get API version."""
    return {
        "version": "2.0.0",
        "name": "Personality Proxy API",
        "ollama_compatible": True,
        "provider": g_data.get("ai_provider_name", "unknown"),
        "features": ["personality", "memory", "tools"]
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    provider = g_data.get("ai_provider")
    memory_db = g_data.get("memory_db")

    return {
        "status": "healthy",
        "provider": g_data.get("ai_provider_name", "unknown"),
        "provider_available": provider is not None,
        "memory_db_available": memory_db is not None,
        "memory_entries": memory_db.get_total_entries() if memory_db else 0
    }


# --- Main Entry Point ---
if __name__ == "__main__":
    import uvicorn

    cfg = ConfigurationFile("config.yml")
    host = cfg.data.get('api', {}).get('host', '0.0.0.0')
    port = cfg.data.get('api', {}).get('port', 11434)  # Default to Ollama port

    logging.info(f"Starting Personality Proxy API on {host}:{port}")

    uvicorn.run(
        "api_server_ollama:app",
        host=host,
        port=port,
        reload=False,
        log_level=log_level_str.lower()
    )
