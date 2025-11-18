"""
Memory service - handles memory retrieval and formatting.
Single responsibility: Memory operations with enhanced hierarchy and decay support.
"""
import logging
from typing import List, Dict, Optional
from lib.services.memory_hierarchy import MemoryHierarchy
from lib.services.memory_decay import MemoryDecayService


class MemoryService:
    """Enhanced service for memory operations with hierarchy support."""

    def __init__(
        self,
        memory_db,
        similarity_threshold: float = 0.65,
        enable_hierarchy: bool = True,
        enable_decay: bool = True
    ):
        """
        Initialize memory service.

        Args:
            memory_db: Neo4j memory database instance
            similarity_threshold: Minimum score for vector memories (default: 0.65)
            enable_hierarchy: Enable memory hierarchy (working/short-term/long-term)
            enable_decay: Enable memory decay scoring
        """
        self.memory_db = memory_db
        self.similarity_threshold = similarity_threshold

        # Initialize hierarchy if enabled
        self.hierarchy = None
        if enable_hierarchy:
            self.hierarchy = MemoryHierarchy(
                memory_db=memory_db,
                similarity_threshold=similarity_threshold
            )
            logging.info("Memory hierarchy enabled")

        # Initialize decay service if enabled
        self.decay_service = None
        if enable_decay:
            self.decay_service = MemoryDecayService()
            logging.info("Memory decay scoring enabled")

    async def retrieve_relevant_memories(
        self,
        query: str,
        user_id: Optional[str] = None,
        top_k: int = 5,
        context_k: int = 20
    ) -> List[Dict]:
        """
        Retrieve relevant memories for a query.

        Uses memory hierarchy if enabled, otherwise falls back to direct database query.

        Args:
            query: Search query
            user_id: User identifier
            top_k: Number of top results to return
            context_k: Context pool size

        Returns:
            List of relevant memories
        """
        if not self.memory_db or (user_id and self.memory_db.get_total_entries(user_id) == 0):
            return []

        try:
            # Use hierarchy if available
            if self.hierarchy and user_id:
                memories = await self.hierarchy.retrieve_memories(
                    query=query,
                    user_id=user_id,
                    top_k=top_k,
                    context_k=context_k
                )
                logging.info(f"Retrieved {len(memories)} memories via hierarchy")
            else:
                # Fallback to direct database query
                memories = self.memory_db.search_with_context(
                    query=query,
                    top_k=top_k,
                    context_k=context_k
                )
                logging.info(f"Retrieved {len(memories)} memories via direct query")

            # Apply decay scoring if enabled
            if self.decay_service and memories:
                memories = self._apply_decay_scoring(memories)

            return memories

        except Exception as e:
            logging.error(f"Error retrieving memories: {e}", exc_info=True)
            return []

    def _apply_decay_scoring(self, memories: List[Dict]) -> List[Dict]:
        """
        Apply decay scoring to memories.

        Args:
            memories: List of memory dictionaries

        Returns:
            Memories with updated relevance scores
        """
        try:
            # Extract memory objects for ranking
            mem_list = []
            for mem in memories:
                mem_obj = mem.get('memory')
                if isinstance(mem_obj, dict):
                    mem_list.append(mem_obj)
                elif hasattr(mem_obj, 'to_dict'):
                    mem_list.append(mem_obj.to_dict())
                else:
                    mem_list.append({'score': mem.get('score', 0.5)})

            # Rank with decay
            ranked = self.decay_service.rank_memories(mem_list)

            # Update original memories with new scores
            for i, mem in enumerate(memories):
                if i < len(ranked):
                    mem['relevance_score'] = ranked[i].get('relevance_score', mem.get('score', 0.5))
                    mem['decay_components'] = ranked[i].get('decay_components', {})

            return memories

        except Exception as e:
            logging.error(f"Error applying decay scoring: {e}", exc_info=True)
            return memories

    def add_to_working_memory(self, content: str, memory_type: str, user_id: str, importance: float = 0.5):
        """
        Add memory to working memory (current conversation context).

        Args:
            content: Memory content
            memory_type: Type of memory
            user_id: User identifier
            importance: Importance score (0-1)
        """
        if self.hierarchy:
            self.hierarchy.add_to_working_memory(content, memory_type, user_id, importance)
            logging.debug(f"Added to working memory: {memory_type}")

    def clear_working_memory(self):
        """Clear working memory (e.g., at end of conversation)."""
        if self.hierarchy:
            self.hierarchy.clear_working_memory()

    def get_stats(self, user_id: Optional[str] = None) -> Dict:
        """
        Get memory service statistics.

        Args:
            user_id: Optional user filter

        Returns:
            Statistics dictionary
        """
        stats = {}

        if self.hierarchy:
            stats['hierarchy'] = self.hierarchy.get_stats(user_id)

        if self.memory_db:
            stats['database_total'] = self.memory_db.get_total_entries(user_id)

        return stats

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
        user_id: Optional[str] = None,
        top_k: int = 5,
        context_k: int = 20
    ) -> Optional[str]:
        """
        Retrieve and format memories in one call.

        Args:
            query: Search query
            user_id: User identifier
            top_k: Number of top results
            context_k: Context pool size

        Returns:
            Formatted memory string or None
        """
        memories = await self.retrieve_relevant_memories(query, user_id, top_k, context_k)
        return self.format_memories(memories)
