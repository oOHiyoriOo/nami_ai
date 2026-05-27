"""
Nami AI — API Server

OpenAI-compatible REST API with personality, memory, and tool support.
External chat adapters (Discord, WhatsApp, …) connect via the persistent
WebSocket endpoint at ``/api/ws/adapter`` and exchange JSON events
bidirectionally — no REST polling required.
"""
import asyncio
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, APIRouter, HTTPException, Depends, WebSocket, Query
from pydantic import BaseModel, Field
from colorama import Fore, init
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from lib.global_registry import g_data
from lib.configuration_file import ConfigurationFile
from lib.services import AppInitializer

init(convert=True, autoreset=True)

# --- Logging Configuration ---
os.makedirs('./logs', exist_ok=True)

cfg_temp = ConfigurationFile.load("config.yml")
log_level_str = cfg_temp.data.get('bot', {}).get('log_level', 'INFO')
log_level = getattr(logging, str(log_level_str).upper(), logging.INFO)

logging.basicConfig(
    level=log_level,
    format=(f'[%(asctime)s] {Fore.YELLOW} {"[%(levelname)s]":<8} {Fore.RESET} [%(name)s] %(message)s'),
    datefmt='%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(f"./logs/api_{time.strftime('%Y-%m-%d_%H_%M_%S')}.log", 'w', 'utf-8'),
        logging.StreamHandler()
    ],
    force=True
)

# asyncssh logs every SSH handshake at INFO — suppress to WARNING to avoid heartbeat noise
logging.getLogger("asyncssh").setLevel(logging.WARNING)

# Suppress /health access logs from uvicorn — healthcheck polls every 10s and spams the log
class _SuppressHealthCheck(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "GET /health " not in record.getMessage()

logging.getLogger("uvicorn.access").addFilter(_SuppressHealthCheck())


# --- Pydantic Models ---

class APIMessage(BaseModel):
    """Single chat message (OpenAI-compatible)."""
    role: str
    content: str
    images: list[str] | None = None
    tool_calls: list[dict] | None = None
    thinking: str | None = Field(None, description="Internal reasoning content (not shown to user)")


class ChatCompletionRequest(BaseModel):
    """Chat completion request (OpenAI-compatible with Nami extensions).

    Standard OpenAI fields are accepted as-is.  The Nami-specific extension
    fields (``user_id``, ``conversation_id``, ``enable_memory``,
    ``enable_personality``, ``think``) are ignored by vanilla OpenAI clients
    and default to sensible values.
    """
    model: str
    messages: list[APIMessage]
    stream: bool = False  # Accepted for API compatibility; returns 501 if true
    tools: list[dict] | None = None
    format: str | None = None
    options: dict[str, Any] | None = None
    # Nami extensions
    user_id: str | None = Field(None, description="User ID for memory/personalisation")
    conversation_id: str | None = Field(None, description="Conversation ID for context")
    enable_memory: bool = Field(True, description="Enable memory system")
    enable_personality: bool = Field(True, description="Enable personality prompt")
    think: bool | None = Field(None, description="Force thinking mode on/off (None = auto-detect)")


class ChatCompletionMessage(BaseModel):
    """Assistant message in an OpenAI chat completion response."""
    role: str = "assistant"
    content: str
    thinking: str | None = None


class ChatCompletionChoice(BaseModel):
    """Single choice in an OpenAI chat completion response."""
    index: int = 0
    message: ChatCompletionMessage
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    """Chat completion response in OpenAI format.

    Includes Nami-specific extension fields (``conversation_id``,
    ``user_id``) that OpenAI clients will simply ignore.
    """
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: dict[str, int]
    # Nami extensions
    conversation_id: str | None = None
    user_id: str | None = None


# --- Application Lifespan ---

initializer = AppInitializer("config.yml")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    await initializer.initialize()
    yield
    await initializer.cleanup()


# --- FastAPI App ---

# --- API Key Authentication ---

security = HTTPBearer(auto_error=False)


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    """Verify API key if configured. Skips auth when no api_key is set.

    When api_key is configured in config.yml, all endpoints require
    an Authorization: Bearer <key> header. Without a configured key,
    authentication is skipped entirely (backward compatible).
    """
    cfg = g_data.get("cfg")
    if cfg is None:
        return  # Skip during startup (config not yet loaded)

    api_key = cfg.data.get('api', {}).get('api_key', '')
    if not api_key:
        return  # No API key configured — skip auth

    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="API key required. Provide Authorization: Bearer <key>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not secrets.compare_digest(credentials.credentials, api_key):
        raise HTTPException(status_code=403, detail="Invalid API key")


app = FastAPI(
    title="Nami AI",
    description=(
        "OpenAI-compatible REST API with personality, long-term memory, and tool support. "
        "Chat adapters connect via WebSocket at /api/ws/adapter."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

# All HTTP routes require API key auth. WebSocket uses its own bridge_secret auth.
http_api = APIRouter(dependencies=[Depends(verify_api_key)])


# --- Endpoints ---

_REST_PIPELINE_TIMEOUT = 120.0  # seconds

@http_api.post("/v1/chat/completions")
async def chat(request: ChatCompletionRequest) -> ChatCompletionResponse:
    """Chat completion endpoint (OpenAI-compatible).

    All requests are routed through the internal EventBus — the same path
    used by Discord, WhatsApp, and every other adapter.  A per-request
    ``asyncio.Future`` bridges the async pub-sub response back to this
    synchronous HTTP call.
    """
    if request.stream:
        raise HTTPException(status_code=501, detail="Streaming is not yet supported. Set stream=false.")

    ws_server = g_data.get("adapter_ws_server")
    event_bus = g_data.get("event_bus")
    if not ws_server or not event_bus:
        raise HTTPException(status_code=503, detail="Service not ready — try again shortly.")

    try:
        conversation_id = request.conversation_id or f"rest-{secrets.token_hex(8)}"
        user_id = request.user_id or "api"
        user_msg = next(
            (m.content for m in reversed(request.messages) if m.role == "user"), ""
        )

        future = ws_server.register_pending_rest(conversation_id)
        try:
            from lib.services.event_bus import Event
            await event_bus.publish(Event(
                type="message.received",
                data={
                    "adapter_name": "rest_api",
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "user_name": user_id,
                    "content": user_msg,
                    "is_dm": True,
                    "history": [
                        {"role": m.role, "content": m.content,
                         "tool_calls": m.tool_calls, "images": m.images}
                        for m in request.messages
                    ],
                    # Per-request overrides forwarded to AIPipelineHandler
                    "model": request.model,
                    "enable_memory": request.enable_memory,
                    "enable_personality": request.enable_personality,
                    "think_override": request.think,
                    "options": request.options,
                    "provider_tool_schemas": request.tools,
                },
            ))

            result_data: dict = await asyncio.wait_for(
                asyncio.shield(future), timeout=_REST_PIPELINE_TIMEOUT
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Pipeline timeout — the model took too long.")
        finally:
            ws_server.unregister_pending_rest(conversation_id)

        return ChatCompletionResponse(
            id=f"chatcmpl-{secrets.token_hex(12)}",
            created=int(time.time()),
            model=request.model,
            choices=[
                ChatCompletionChoice(
                    message=ChatCompletionMessage(
                        content=result_data.get("content", ""),
                        thinking=result_data.get("thinking"),
                    )
                )
            ],
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            conversation_id=conversation_id,
            user_id=user_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@http_api.get("/v1/models")
async def openai_list_models():
    """List available models in OpenAI-compatible format."""
    try:
        model_cache = g_data.get("model_cache")
        data = []

        if model_cache:
            for m in model_cache.to_ollama_format():
                data.append({
                    "id": m["name"],
                    "object": "model",
                    "created": 0,
                    "owned_by": "nami",
                })

        return {"object": "list", "data": data}

    except Exception as e:
        logging.error(f"List models (OpenAI format) error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@http_api.get("/api/models/stats")
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
        raise HTTPException(status_code=500, detail="Internal server error")


@http_api.get("/api/version")
@http_api.get("/")
async def version():
    """API version and capability info."""
    cfg = g_data.get("cfg")
    providers = list(cfg.data.get('providers', {}).keys()) if cfg else []

    return {
        "version": "2.0.0",
        "name": "Nami AI",
        "api_format": "openai",
        "model_format": "<provider>/<model>",
        "available_providers": providers,
        "features": ["personality", "memory", "tools", "multi-provider", "websocket-adapters"],
    }


@http_api.get("/health")
async def health():
    """Health check endpoint."""
    cfg = g_data.get("cfg")
    memory_db = g_data.get("memory_db")
    providers = list(cfg.data.get('providers', {}).keys()) if cfg else []

    memory_entries = 0
    db_error = None
    if memory_db:
        try:
            memory_entries = await memory_db.get_total_entries()
        except Exception as e:
            db_error = str(e)

    ws_server = g_data.get("adapter_ws_server")
    connected_adapters = ws_server.connected_adapters if ws_server else []

    return {
        "status": "healthy" if db_error is None else "degraded",
        "available_providers": providers,
        "memory_db_available": memory_db is not None,
        "memory_entries": memory_entries,
        "connected_adapters": connected_adapters,
        "error": db_error,
    }


@app.websocket("/api/ws/adapter")
async def adapter_websocket(
    websocket: WebSocket,
    name: str = Query(..., description="Adapter identifier (e.g. 'discord', 'whatsapp')"),
    secret: str = Query(..., description="Bridge secret configured in adapters.<name>.bridge_secret"),
):
    """WebSocket endpoint for external chat adapters.

    Adapters connect here and exchange JSON events bidirectionally.
    Authentication is via ``name`` + ``secret`` query parameters.
    """
    ws_server = g_data.get("adapter_ws_server")
    if not ws_server:
        await websocket.close(code=1013, reason="Server not ready")
        return

    await ws_server.handle_connection(websocket, name, secret)


@http_api.get("/api/memory/analytics")
async def memory_analytics(user_id: str | None = None):
    """
    Memory system analytics and diagnostics.

    Returns structured JSON with health metrics, age distribution,
    access patterns, concept distribution, and a list of diagnosed issues.

    Args:
        user_id: Optional user filter; omit to see global stats.
    """
    analytics = g_data.get("memory_analytics")
    if not analytics:
        raise HTTPException(status_code=503, detail="Memory analytics not available")

    return {
        "health": await analytics.get_system_health(user_id),
        "age_distribution": await analytics.get_memory_age_distribution(user_id),
        "access_patterns": await analytics.get_access_patterns(user_id),
        "concept_distribution": await analytics.get_concept_distribution(user_id, top_k=10),
        "diagnosis": await analytics.diagnose_issues(user_id),
    }


app.include_router(http_api)


# --- Main Entry Point ---

if __name__ == "__main__":
    import uvicorn

    cfg = ConfigurationFile.load("config.yml")
    host = cfg.data.get('api', {}).get('host', '127.0.0.1')
    port = cfg.data.get('api', {}).get('port', 11434)

    logging.info(f"Starting Personality Proxy API on {host}:{port}")

    uvicorn.run(
        "api_server:app",
        host=host,
        port=port,
        reload=False,
        log_level=log_level_str.lower()
    )
