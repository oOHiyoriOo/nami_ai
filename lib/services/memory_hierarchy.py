"""
Memory hierarchy service - implements multi-tier memory system.
Provides long-term memory (Neo4j persistent storage).
"""
import asyncio
import logging
import time
from typing import Any
from dataclasses import dataclass, field


@dataclass
class MemoryEntry:
    """Represents a memory entry with metadata."""
    content: str
    memory_type: str
    score: float = 0.0
    timestamp: float = field(default_factory=time.time)
    access_count: int = 0
    importance: float = 0.5
    user_id: str | None = None
    memory_id: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary format."""
        return {
            'text': self.content,
            'type': self.memory_type,
            'score': self.score,
            'timestamp': self.timestamp,
            'access_count': self.access_count,
            'importance': self.importance,
            'user_id': self.user_id,
            'memory_id': self.memory_id
        }


class MemoryHierarchy:
    """Memory system backed by Neo4j long-term storage."""

    def __init__(
        self,
        memory_db,
        similarity_threshold: float = 0.65
    ):
        """
        Initialize memory hierarchy.

        Args:
            memory_db: Long-term memory database (Neo4j)
            similarity_threshold: Minimum similarity score for memories
        """
        self.long_term_memory = memory_db
        self.similarity_threshold = similarity_threshold
        logging.info(f"Memory hierarchy initialized")

    async def retrieve_memories(
        self,
        query: str,
        user_id: str,
        top_k: int = 5,
        context_k: int = 20
    ) -> list[dict]:
        """
        Retrieve memories from long-term storage.

        Args:
            query: Search query
            user_id: User identifier
            top_k: Number of results to return
            context_k: Context pool size for long-term search

        Returns:
            List of memory dictionaries with relevance scores
        """
        all_memories = []

        if self.long_term_memory and await self.long_term_memory.get_total_entries() > 0:
            try:
                lt_memories = await self.long_term_memory.search_with_context(
                    query=query,
                    filter_user_id=user_id,
                    top_k=top_k,
                    context_k=context_k
                )

                # Format long-term memories
                recalled_ids = []
                for mem in lt_memories:
                    mem_obj = mem.get('memory')
                    mem_id = getattr(mem_obj, 'id', None) if not isinstance(mem_obj, dict) else mem_obj.get('id')
                    mem_type = type(mem_obj).__name__ if not isinstance(mem_obj, dict) else None
                    mem_dict = {
                        'text': self._extract_memory_text(mem_obj),
                        'type': mem.get('type', 'vector'),
                        'score': mem.get('score', 0.0),
                        'tier': 'long_term',
                        'memory_id': mem_id,
                        'memory_type': mem_type
                    }
                    all_memories.append(mem_dict)
                    # Track vector-matched memories for access count increment (recall resistance)
                    if mem_id and mem_type and mem.get('type') == 'vector':
                        recalled_ids.append((mem_id, mem_type))

                # Increment access counts in background (recall resistance for decay scoring)
                if recalled_ids:
                    asyncio.create_task(self._increment_access_counts(recalled_ids))

                logging.info(f"Retrieved {len(lt_memories)} memories from long-term storage")

            except Exception as e:
                logging.error(f"Error retrieving from long-term memory: {e}", exc_info=True)

        # Filter by threshold and sort by score
        filtered = [
            m for m in all_memories
            if m.get('score', 0.0) >= self.similarity_threshold
        ]
        filtered.sort(key=lambda x: x.get('score', 0.0), reverse=True)

        return filtered[:top_k]

    def _extract_memory_text(self, memory_obj) -> str:
        """Extract text from memory object."""
        if isinstance(memory_obj, dict):
            return memory_obj.get('summary') or memory_obj.get('statement') or memory_obj.get('description') or str(memory_obj)
        elif hasattr(memory_obj, 'summary'):
            return memory_obj.summary
        elif hasattr(memory_obj, 'statement'):
            return memory_obj.statement
        elif hasattr(memory_obj, 'description'):
            return memory_obj.description
        else:
            return str(memory_obj)

    async def _increment_access_counts(self, memory_ids: list[tuple[str, str]]):
        """
        Increment access_count on recalled memories in Neo4j (recall resistance).

        Called as a fire-and-forget background task after retrieving from long-term
        storage. Memories that are frequently recalled get a higher access_boost in
        decay scoring (20% weight), making them resist decay over time.

        Args:
            memory_ids: List of (memory_id, memory_type) tuples to update
        """
        try:
            driver = self.long_term_memory.get_driver()
            async with driver.session() as session:
                for mem_id, mem_type in memory_ids:
                    await session.run(
                        f"MATCH (m:{mem_type} {{id: $id}}) "
                        "SET m.access_count = COALESCE(m.access_count, 0) + 1, "
                        "    m.lastAccessedTimestamp = timestamp()",
                        id=mem_id
                    )
            logging.debug(f"Incremented access_count for {len(memory_ids)} recalled memories")
        except Exception as e:
            logging.warning(f"Failed to increment access counts: {e}")

    async def get_stats(self, user_id: str | None = None) -> dict:
        """
        Get memory hierarchy statistics.

        Returns long-term total.
        """
        long_term_total = 0
        if self.long_term_memory:
            try:
                driver = self.long_term_memory.get_driver()
                async with driver.session() as session:
                    if user_id:
                        result = await session.run(
                            "MATCH (u:Person {id: $uid})-[:IS_AUTHOR_OF]->(m) "
                            "WHERE m:EpisodicMemory OR m:KnowledgeUnit OR m:ProceduralUnit "
                            "RETURN count(m) AS c",
                            uid=user_id,
                        )
                    else:
                        result = await session.run(
                            "MATCH (m) WHERE m:EpisodicMemory OR m:KnowledgeUnit "
                            "OR m:ProceduralUnit RETURN count(m) AS c"
                        )
                    record = await result.single()
                    long_term_total = record["c"] if record else 0
            except Exception as e:
                logging.warning(f"[memory_hierarchy] get_stats DB error: {e}")

        return {
            "long_term_total": long_term_total,
        }
