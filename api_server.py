"""
Personality Proxy API Server
FastAPI server that exposes the personality and memory system via OpenAPI.
"""
import logging
import os
import time
import json
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from colorama import Fore, init

from api.models import (
    ChatRequest,
    ChatResponse,
    MemoryCreate,
    MemorySearchRequest,
    MemorySearchResponse,
    MemoryItem,
    ConversationHistory,
    ChatMessage,
    HealthResponse,
    PersonalityInfo
)
from api.conversation_service import ConversationService
from lib.memory_db import MemoryDb
from lib.tool_loader import load_tools
from lib.global_registry import g_data
from lib.asyncsqlite import AsyncSQLite
from lib.configurationFile import ConfigurationFile
from lib.system_prompt_parser import NamiSystemPrompt

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


# --- Application Lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    logging.info("Initializing Personality Proxy API...")

    # Load configuration
    cfg = g_data.get_or_create("cfg", ConfigurationFile, "config.yml")
    system_prompt_filename = cfg.data['ollama']['system_prompt']
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

    # Load tools (pass None for client as we don't have Discord)
    loaded_tools = await load_tools(None)
    g_data.get_or_create("tools", lambda: loaded_tools)
    logging.info(f"Loaded {len(loaded_tools)} tools globally.")

    # Initialize conversation service
    conversation_service = ConversationService(cfg.data)
    g_data.get_or_create("conversation_service", lambda: conversation_service)

    logging.info("="*70)
    logging.info(f"{Fore.GREEN}Personality Proxy API initialized successfully!")
    logging.info(f"Personality: {system_prompt_filename}")
    logging.info(f"Model: {cfg.data['ollama']['model']}")
    logging.info(f"Neo4j: {cfg.data['neo4j']['uri']}")
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
    description="OpenAPI interface for AI personality with Neo4j memory system",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure this appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- API Endpoints ---

@app.get("/", tags=["General"])
async def root():
    """Root endpoint with API information."""
    return {
        "name": "Personality Proxy API",
        "version": "1.0.0",
        "status": "operational",
        "docs": "/docs",
        "openapi": "/openapi.json"
    }


@app.get("/health", response_model=HealthResponse, tags=["General"])
async def health_check():
    """Check the health status of the API and its dependencies."""
    cfg = g_data.get("cfg")
    memory_db = g_data.get("memory_db")

    neo4j_connected = False
    ollama_available = False

    try:
        # Check Neo4j connection
        if memory_db:
            memory_db.get_total_entries()
            neo4j_connected = True
    except Exception as e:
        logging.error(f"Neo4j health check failed: {e}")

    try:
        # Check Ollama availability
        from ollama import Client as Ollama
        ollama_client = Ollama(host=cfg.data['ollama']['url'])
        ollama_client.list()
        ollama_available = True
    except Exception as e:
        logging.error(f"Ollama health check failed: {e}")

    return HealthResponse(
        status="healthy" if (neo4j_connected and ollama_available) else "degraded",
        neo4j_connected=neo4j_connected,
        ollama_available=ollama_available
    )


@app.get("/personality", response_model=PersonalityInfo, tags=["General"])
async def get_personality_info():
    """Get information about the current personality."""
    cfg = g_data.get("cfg")
    sys_prompt = g_data.get("system_prompt")
    tools = g_data.get("tools")

    full_prompt = await sys_prompt.get_prompt()
    preview = full_prompt[:500] + "..." if len(full_prompt) > 500 else full_prompt

    tool_names = [tool['function']['name'] for tool in tools] if tools else []

    return PersonalityInfo(
        name=cfg.data['ollama']['system_prompt'],
        description=f"AI personality powered by {cfg.data['ollama']['model']}",
        available_tools=tool_names,
        system_prompt_preview=preview
    )


@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(request: ChatRequest):
    """
    Send a message to the personality and get a response.

    The personality will use its memory system and available tools to provide
    contextually aware responses.
    """
    conversation_service = g_data.get("conversation_service")

    if not conversation_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Conversation service not initialized"
        )

    try:
        # Use conversation_id or generate one from user_id
        conv_id = request.conversation_id or f"user_{request.user_id}"

        result = await conversation_service.chat(
            message=request.message,
            user_id=request.user_id,
            user_name=request.user_id,  # Can be enhanced with actual names
            conversation_id=conv_id,
            max_history=request.max_history,
            include_thinking=request.include_thinking
        )

        return ChatResponse(
            response=result["response"],
            thinking=result.get("thinking"),
            tools_used=result["tools_used"],
            conversation_id=conv_id
        )

    except Exception as e:
        logging.error(f"Error processing chat request: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing chat: {str(e)}"
        )


@app.get("/conversations/{conversation_id}/history", response_model=ConversationHistory, tags=["Chat"])
async def get_conversation_history(conversation_id: str, limit: int = 50):
    """
    Get the conversation history for a specific conversation.
    """
    conversation_service = g_data.get("conversation_service")

    if not conversation_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Conversation service not initialized"
        )

    try:
        messages = await conversation_service.get_conversation_history(
            conversation_id=conversation_id,
            limit=limit
        )

        chat_messages = [
            ChatMessage(
                role=msg["role"],
                content=msg["content"],
                timestamp=msg.get("timestamp")
            )
            for msg in messages
        ]

        return ConversationHistory(
            conversation_id=conversation_id,
            messages=chat_messages,
            total_messages=len(chat_messages)
        )

    except Exception as e:
        logging.error(f"Error retrieving conversation history: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving history: {str(e)}"
        )


@app.post("/memories/search", response_model=MemorySearchResponse, tags=["Memory"])
async def search_memories(request: MemorySearchRequest):
    """
    Search for memories in the Neo4j database.
    """
    memory_db = g_data.get("memory_db")

    if not memory_db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Memory database not initialized"
        )

    try:
        # Use search_with_context for better results
        results = memory_db.search_with_context(
            query=request.query,
            top_k=request.limit,
            context_k=20
        )

        memories = []
        for mem in results:
            if mem.get('score', 1.0) >= request.min_relevance:
                memories.append(MemoryItem(
                    id=mem.get('id', 'unknown'),
                    content=mem.get('text', ''),
                    memory_type=mem.get('type', 'unknown'),
                    relevance_score=mem.get('score'),
                    metadata=mem
                ))

        return MemorySearchResponse(
            memories=memories,
            count=len(memories)
        )

    except Exception as e:
        logging.error(f"Error searching memories: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error searching memories: {str(e)}"
        )


@app.post("/memories", tags=["Memory"])
async def create_memory(memory: MemoryCreate):
    """
    Create a new memory in the Neo4j database.
    """
    memory_db = g_data.get("memory_db")

    if not memory_db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Memory database not initialized"
        )

    try:
        import uuid
        memory_args = {
            "id": str(uuid.uuid4()),
            "summary": memory.content,
            "authorUserId": memory.user_id,
            "creationTimestamp": int(time.time() * 1000)
        }

        if memory.metadata:
            memory_args.update(memory.metadata)

        if memory.emotional_context:
            memory_args["emotionalContext"] = memory.emotional_context

        memory_db.add_memory(
            user_id=memory.user_id,
            user_name=memory.user_id,
            memory_type=memory.memory_type,
            memory_args=memory_args
        )

        return {
            "status": "success",
            "message": f"Memory created successfully",
            "memory_type": memory.memory_type
        }

    except Exception as e:
        logging.error(f"Error creating memory: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating memory: {str(e)}"
        )


@app.get("/memories/stats", tags=["Memory"])
async def get_memory_stats():
    """
    Get statistics about the memory database.
    """
    memory_db = g_data.get("memory_db")

    if not memory_db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Memory database not initialized"
        )

    try:
        total_entries = memory_db.get_total_entries()

        return {
            "total_memories": total_entries,
            "status": "operational"
        }

    except Exception as e:
        logging.error(f"Error getting memory stats: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting memory stats: {str(e)}"
        )


# --- Main Entry Point ---
if __name__ == "__main__":
    import uvicorn

    # Get host and port from config or use defaults
    cfg = ConfigurationFile("config.yml")
    host = cfg.data.get('api', {}).get('host', '0.0.0.0')
    port = cfg.data.get('api', {}).get('port', 8000)

    logging.info(f"Starting API server on {host}:{port}")

    uvicorn.run(
        "api_server:app",
        host=host,
        port=port,
        reload=False,
        log_level=log_level_str.lower()
    )
