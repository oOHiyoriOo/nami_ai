"""
API models for the personality proxy.
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


class ChatMessage(BaseModel):
    """A single chat message."""
    role: str = Field(..., description="Role of the message sender (user/assistant/system)")
    content: str = Field(..., description="Content of the message")
    timestamp: Optional[datetime] = Field(None, description="Timestamp of the message")


class ChatRequest(BaseModel):
    """Request to send a message to the personality."""
    message: str = Field(..., description="The user's message")
    user_id: str = Field(..., description="Unique identifier for the user")
    conversation_id: Optional[str] = Field(None, description="Conversation/channel ID for context")
    include_thinking: bool = Field(False, description="Include AI's thinking process in response")
    max_history: int = Field(64, description="Maximum number of historical messages to include")


class ChatResponse(BaseModel):
    """Response from the personality."""
    response: str = Field(..., description="The personality's response")
    thinking: Optional[str] = Field(None, description="The AI's thinking process (if requested)")
    tools_used: List[str] = Field(default_factory=list, description="List of tools used during response generation")
    conversation_id: str = Field(..., description="Conversation ID for this exchange")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Response timestamp")


class MemoryCreate(BaseModel):
    """Request to create a memory."""
    memory_type: str = Field(..., description="Type of memory: episodic, knowledge, or procedural")
    content: str = Field(..., description="The memory content")
    user_id: str = Field(..., description="User associated with this memory")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata for the memory")
    importance: Optional[float] = Field(0.5, description="Importance score (0-1)")
    emotional_context: Optional[str] = Field(None, description="Emotional context for episodic memories")


class MemorySearchRequest(BaseModel):
    """Request to search memories."""
    query: str = Field(..., description="Search query")
    user_id: Optional[str] = Field(None, description="Filter by user ID")
    memory_type: Optional[str] = Field(None, description="Filter by memory type")
    limit: int = Field(10, description="Maximum number of results")
    min_relevance: float = Field(0.7, description="Minimum relevance score")


class MemoryItem(BaseModel):
    """A memory item from the database."""
    id: str = Field(..., description="Memory ID")
    content: str = Field(..., description="Memory content")
    memory_type: str = Field(..., description="Type of memory")
    relevance_score: Optional[float] = Field(None, description="Relevance score for searches")
    created_at: Optional[str] = Field(None, description="Creation timestamp")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class MemorySearchResponse(BaseModel):
    """Response from memory search."""
    memories: List[MemoryItem] = Field(..., description="List of matching memories")
    count: int = Field(..., description="Number of memories returned")


class ConversationHistory(BaseModel):
    """Conversation history."""
    conversation_id: str = Field(..., description="Conversation ID")
    messages: List[ChatMessage] = Field(..., description="List of messages in the conversation")
    total_messages: int = Field(..., description="Total number of messages")


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = Field(..., description="Service status")
    neo4j_connected: bool = Field(..., description="Neo4j connection status")
    ollama_available: bool = Field(..., description="Ollama availability status")
    version: str = Field("1.0.0", description="API version")


class PersonalityInfo(BaseModel):
    """Information about the current personality."""
    name: str = Field(..., description="Personality name")
    description: str = Field(..., description="Personality description")
    available_tools: List[str] = Field(..., description="Available tools for this personality")
    system_prompt_preview: str = Field(..., description="First 500 chars of system prompt")
