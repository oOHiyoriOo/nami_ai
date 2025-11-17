"""
Personality Proxy API Server
Ollama-compatible API with personality and memory features.
"""
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from colorama import Fore, init

from lib.global_registry import g_data
from lib.configurationFile import ConfigurationFile
from lib.services import AppInitializer
from lib.ai_providers import Message

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


# --- Pydantic Models ---

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


class OllamaGenerateRequest(BaseModel):
    """Generate request compatible with Ollama API."""
    model: str
    prompt: str
    stream: bool = False
    system: Optional[str] = None
    user_id: Optional[str] = None
    enable_memory: bool = True
    enable_personality: bool = True


class OllamaTagsResponse(BaseModel):
    """Response for listing models."""
    models: List[Dict[str, Any]]


# --- Application Lifespan ---

initializer = AppInitializer("config.yml")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    await initializer.initialize()
    yield
    await initializer.cleanup()


# --- FastAPI App ---

app = FastAPI(
    title="Personality Proxy API",
    description="Ollama-compatible API with personality and memory features",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Helper Functions ---

def convert_to_provider_messages(messages: List[OllamaMessage]) -> List[Message]:
    """Convert Ollama messages to provider format."""
    return [
        Message(
            role=msg.role,
            content=msg.content,
            tool_calls=msg.tool_calls
        )
        for msg in messages
    ]


def convert_to_dict_messages(messages: List[OllamaMessage]) -> List[dict]:
    """Convert Ollama messages to dict format."""
    return [
        {
            "role": msg.role,
            "content": msg.content,
            "tool_calls": msg.tool_calls
        }
        for msg in messages
    ]


def get_default_tools() -> Optional[List[Dict]]:
    """Get default tools if available."""
    loaded_tools = g_data.get("tools")
    if loaded_tools:
        return [{k: v for k, v in tool.items() if k != 'func'} for tool in loaded_tools]
    return None


async def build_enhanced_context(
    messages: List[OllamaMessage],
    user_id: str,
    enable_memory: bool,
    enable_personality: bool
) -> List[Message]:
    """Build enhanced context with personality and memories."""
    context_builder = g_data.get("context_builder")

    # Convert messages to dict format
    dict_messages = convert_to_dict_messages(messages)

    # Build context
    enhanced = await context_builder.build_context(
        messages=dict_messages,
        user_id=user_id,
        enable_personality=enable_personality,
        enable_memory=enable_memory
    )

    # Convert back to provider format
    return [
        Message(
            role=msg["role"],
            content=msg["content"],
            tool_calls=msg.get("tool_calls")
        )
        for msg in enhanced
    ]


# --- Endpoints ---

@app.post("/api/chat")
@app.post("/v1/chat/completions")
async def chat(request: OllamaChatRequest):
    """Chat completion endpoint (Ollama-compatible)."""
    provider = g_data.get("ai_provider")
    if not provider:
        raise HTTPException(status_code=503, detail="AI provider not initialized")

    try:
        start_time = time.time()

        # Get user/conversation IDs
        user_id = request.user_id or "anonymous"

        # Build enhanced context
        enhanced_messages = await build_enhanced_context(
            request.messages,
            user_id,
            request.enable_memory,
            request.enable_personality
        )

        # Get tools
        tools = request.tools if request.tools is not None else get_default_tools()

        # Handle streaming
        if request.stream:
            return await handle_streaming_chat(provider, request.model, enhanced_messages, tools)

        # Non-streaming response
        response = await provider.chat(enhanced_messages, tools, model=request.model)
        duration = int((time.time() - start_time) * 1000000000)

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


async def handle_streaming_chat(provider, model: str, messages: List[Message], tools: Optional[List[Dict]]):
    """Handle streaming chat response."""
    async def generate_stream():
        async for chunk in provider.chat_stream(messages, tools, model=model):
            response_chunk = {
                "model": model,
                "created_at": datetime.utcnow().isoformat() + "Z",
                "message": {"role": "assistant", "content": chunk},
                "done": False
            }
            yield f"{response_chunk}\n"

        # Final chunk
        final = {
            "model": model,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "message": {"role": "assistant", "content": ""},
            "done": True
        }
        yield f"{final}\n"

    return StreamingResponse(generate_stream(), media_type="application/x-ndjson")


@app.post("/api/generate")
async def generate(request: OllamaGenerateRequest):
    """Generate completion endpoint (Ollama-compatible)."""
    # Convert to chat format
    messages = []

    if request.system:
        messages.append(OllamaMessage(role="system", content=request.system))

    messages.append(OllamaMessage(role="user", content=request.prompt))

    # Create chat request
    chat_request = OllamaChatRequest(
        model=request.model,
        messages=messages,
        stream=request.stream,
        user_id=request.user_id,
        enable_memory=request.enable_memory,
        enable_personality=request.enable_personality if not request.system else False
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
    port = cfg.data.get('api', {}).get('port', 11434)

    logging.info(f"Starting Personality Proxy API on {host}:{port}")

    uvicorn.run(
        "api_server:app",
        host=host,
        port=port,
        reload=False,
        log_level=log_level_str.lower()
    )
