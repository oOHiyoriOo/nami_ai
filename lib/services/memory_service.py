"""
Memory service - handles memory retrieval and formatting.
Single responsibility: Memory operations.
"""
import logging
from typing import List, Dict, Optional


class MemoryService:
    """Service for memory operations."""

    def __init__(self, memory_db, similarity_threshold: float = 0.65):
        """
        Initialize memory service.

        Args:
            memory_db: Neo4j memory database instance
            similarity_threshold: Minimum score for vector memories (default: 0.65)
        """
        self.memory_db = memory_db
        self.similarity_threshold = similarity_threshold

    async def retrieve_relevant_memories(
        self,
        query: str,
        top_k: int = 5,
        context_k: int = 20
    ) -> List[Dict]:
        """
        Retrieve relevant memories for a query.

        Args:
            query: Search query
            top_k: Number of top results to return
            context_k: Context pool size

        Returns:
            List of relevant memories
        """
        if not self.memory_db or self.memory_db.get_total_entries() == 0:
            return []

        try:
            memories = self.memory_db.search_with_context(
                query=query,
                top_k=top_k,
                context_k=context_k
            )
            return memories
        except Exception as e:
            logging.error(f"Error retrieving memories: {e}", exc_info=True)
            return []

    def format_memories(self, memories: List[Dict]) -> Optional[str]:
        """
        Format memories into a readable string.

        Args:
            memories: List of memory dictionaries

        Returns:
            Formatted memory string or None
        """
        if not memories:
            return None

        formatted = []
        for mem in memories:
            mem_text = mem.get('text')
            mem_type = mem.get('type')
            mem_score = mem.get('score', 0.0)

            if not mem_text:
                continue

            # Include context memories or vector memories above threshold
            if mem_type == 'context' or (mem_type == 'vector' and mem_score >= self.similarity_threshold):
                score_info = f"(Score: {mem_score:.2f})" if mem_type == 'vector' else "(Context)"
                formatted.append(f"- {mem_text} {score_info}")

        if not formatted:
            return None

        logging.info(f"Formatted {len(formatted)} relevant memories")
        return "Relevant memories:\n" + "\n".join(formatted)

    async def get_formatted_memories(
        self,
        query: str,
        top_k: int = 5,
        context_k: int = 20
    ) -> Optional[str]:
        """
        Retrieve and format memories in one call.

        Args:
            query: Search query
            top_k: Number of top results
            context_k: Context pool size

        Returns:
            Formatted memory string or None
        """
        memories = await self.retrieve_relevant_memories(query, top_k, context_k)
        return self.format_memories(memories)
