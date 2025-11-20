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

def parse_model_string(model: str) -> tuple:
    """
    Parse model string in format <provider>/<model>.

    Args:
        model: Model string (e.g., "ollama/llama2", "copilot/gpt-4.1")

    Returns:
        Tuple of (provider_name, model_name)

    Raises:
        ValueError: If model format is invalid
    """
    if '/' not in model:
        raise ValueError(
            f"Invalid model format: '{model}'. "
            "Expected format: <provider>/<model> (e.g., 'ollama/llama2', 'copilot/gpt-4.1')"
        )

    parts = model.split('/', 1)
    return parts[0], parts[1]


def get_or_create_provider(provider_name: str):
    """
    Get or create a provider instance.

    Args:
        provider_name: Name of the provider

    Returns:
        Provider instance

    Raises:
        HTTPException: If provider not configured or initialization fails
    """
    from lib.ai_providers import ProviderRegistry

    # Check if provider already exists in cache
    cache_key = f"provider_{provider_name}"
    cached_provider = g_data.get(cache_key)
    if cached_provider:
        return cached_provider

    # Get config
    cfg = g_data.get("cfg")
    if not cfg:
        raise HTTPException(status_code=503, detail="Configuration not loaded")

    provider_config = cfg.data.get('providers', {}).get(provider_name)
    if not provider_config:
        available = list(cfg.data.get('providers', {}).keys())
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider_name}' not configured. Available providers: {available}"
        )

    # Create provider
    try:
        provider = ProviderRegistry.get_provider(provider_name, provider_config)
        g_data.get_or_create(cache_key, lambda: provider)
        logging.info(f"Initialized provider: {provider_name}")
        return provider
    except Exception as e:
        logging.error(f"Failed to initialize provider '{provider_name}': {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Failed to initialize provider '{provider_name}': {str(e)}"
        )


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
    try:
        start_time = time.time()

        # Parse model string to get provider and model name
        try:
            provider_name, model_name = parse_model_string(request.model)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        # Get or create provider
        provider = get_or_create_provider(provider_name)

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
            return await handle_streaming_chat(provider, model_name, enhanced_messages, tools, request.model)

        # Non-streaming response
        response = await provider.chat(enhanced_messages, tools, model=model_name)
        duration = int((time.time() - start_time) * 1000000000)

        # Record successful model usage
        model_cache = g_data.get("model_cache")
        if model_cache:
            model_cache.record_success(request.model)

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

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def handle_streaming_chat(provider, model: str, messages: List[Message], tools: Optional[List[Dict]], full_model: str):
    """Handle streaming chat response."""
    async def generate_stream():
        success = False
        try:
            async for chunk in provider.chat_stream(messages, tools, model=model):
                success = True  # At least one chunk received
                response_chunk = {
                    "model": full_model,
                    "created_at": datetime.utcnow().isoformat() + "Z",
                    "message": {"role": "assistant", "content": chunk},
                    "done": False
                }
                yield f"{response_chunk}\n"

            # Record successful streaming
            if success:
                model_cache = g_data.get("model_cache")
                if model_cache:
                    model_cache.record_success(full_model)

            # Final chunk
            final = {
                "model": full_model,
                "created_at": datetime.utcnow().isoformat() + "Z",
                "message": {"role": "assistant", "content": ""},
                "done": True
            }
            yield f"{final}\n"
        except Exception as e:
            logging.error(f"Streaming error: {e}", exc_info=True)
            raise

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
    """List available models (Ollama-compatible) - returns cached successfully used models."""
    try:
        cfg = g_data.get("cfg")
        if not cfg:
            raise HTTPException(status_code=503, detail="Configuration not loaded")

        model_cache = g_data.get("model_cache")

        # Return cached models if available
        if model_cache:
            cached_models = model_cache.to_ollama_format()
            if cached_models:
                logging.debug(f"Returning {len(cached_models)} cached models")
                return OllamaTagsResponse(models=cached_models)
            else:
                logging.info("No cached models yet, returning empty list")
                return OllamaTagsResponse(models=[])

        # Fallback: return empty list if cache not available
        logging.warning("Model cache not available")
        return OllamaTagsResponse(models=[])

    except Exception as e:
        logging.error(f"List models error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/models/stats")
async def model_cache_stats():
    """Get model cache statistics."""
    try:
        model_cache = g_data.get("model_cache")
        if not model_cache:
            raise HTTPException(status_code=503, detail="Model cache not available")

        stats = model_cache.get_cache_stats()
        return {
            "status": "ok",
            "cache": stats
        }

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Cache stats error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/version")
@app.get("/")
async def version():
    """Get API version."""
    cfg = g_data.get("cfg")
    providers = list(cfg.data.get('providers', {}).keys()) if cfg else []

    return {
        "version": "2.0.0",
        "name": "Personality Proxy API",
        "ollama_compatible": True,
        "model_format": "<provider>/<model>",
        "available_providers": providers,
        "features": ["personality", "memory", "tools", "multi-provider"]
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    cfg = g_data.get("cfg")
    memory_db = g_data.get("memory_db")
    providers = list(cfg.data.get('providers', {}).keys()) if cfg else []

    return {
        "status": "healthy",
        "available_providers": providers,
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
