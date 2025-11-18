"""
Memory hierarchy service - implements multi-tier memory system.
Provides working memory (current context), short-term memory (session cache),
and long-term memory (Neo4j persistent storage).
"""
import logging
import time
from typing import List, Dict, Optional
from collections import OrderedDict
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
    user_id: Optional[str] = None
    memory_id: Optional[str] = None

    def to_dict(self) -> Dict:
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


class TTLCache:
    """Time-To-Live cache with automatic expiration."""

    def __init__(self, maxsize: int = 100, ttl: int = 3600):
        """
        Initialize TTL cache.

        Args:
            maxsize: Maximum number of entries
            ttl: Time to live in seconds
        """
        self.maxsize = maxsize
        self.ttl = ttl
        self.cache = OrderedDict()
        self.timestamps = {}

    def get(self, key: str) -> Optional[any]:
        """Get value from cache if not expired."""
        if key not in self.cache:
            return None

        # Check if expired
        if time.time() - self.timestamps[key] > self.ttl:
            self.remove(key)
            return None

        # Move to end (most recently used)
        self.cache.move_to_end(key)
        return self.cache[key]

    def set(self, key: str, value: any):
        """Set value in cache."""
        if key in self.cache:
            self.cache.move_to_end(key)
        else:
            if len(self.cache) >= self.maxsize:
                # Remove oldest item
                self.cache.popitem(last=False)

        self.cache[key] = value
        self.timestamps[key] = time.time()

    def remove(self, key: str):
        """Remove key from cache."""
        if key in self.cache:
            del self.cache[key]
            del self.timestamps[key]

    def clear(self):
        """Clear all entries."""
        self.cache.clear()
        self.timestamps.clear()

    def items(self):
        """Get all items."""
        return self.cache.items()


class WorkingMemory:
    """Working memory - stores current conversation context."""

    def __init__(self, max_entries: int = 20):
        """
        Initialize working memory.

        Args:
            max_entries: Maximum number of memories to keep in working memory
        """
        self.max_entries = max_entries
        self.memories = []

    def add(self, memory: MemoryEntry):
        """Add memory to working memory."""
        self.memories.append(memory)

        # Keep only the most recent entries
        if len(self.memories) > self.max_entries:
            self.memories = self.memories[-self.max_entries:]

    def get_all(self) -> List[MemoryEntry]:
        """Get all memories in working memory."""
        return self.memories

    def clear(self):
        """Clear working memory."""
        self.memories = []

    def get_count(self) -> int:
        """Get number of entries in working memory."""
        return len(self.memories)


class ShortTermMemory:
    """Short-term memory - session-based cache with TTL."""

    def __init__(self, cache_size: int = 200, ttl_seconds: int = 3600):
        """
        Initialize short-term memory.

        Args:
            cache_size: Maximum number of cached memories per user
            ttl_seconds: Time to live for cached memories
        """
        self.user_caches = {}
        self.cache_size = cache_size
        self.ttl_seconds = ttl_seconds

    def _get_user_cache(self, user_id: str) -> TTLCache:
        """Get or create cache for user."""
        if user_id not in self.user_caches:
            self.user_caches[user_id] = TTLCache(
                maxsize=self.cache_size,
                ttl=self.ttl_seconds
            )
        return self.user_caches[user_id]

    def add(self, user_id: str, query: str, memories: List[Dict]):
        """Cache memories for a query."""
        cache = self._get_user_cache(user_id)
        cache.set(query, memories)

    def get(self, user_id: str, query: str) -> Optional[List[Dict]]:
        """Get cached memories for a query."""
        cache = self._get_user_cache(user_id)
        return cache.get(query)

    def clear_user(self, user_id: str):
        """Clear cache for specific user."""
        if user_id in self.user_caches:
            self.user_caches[user_id].clear()

    def clear_all(self):
        """Clear all user caches."""
        self.user_caches.clear()


class MemoryHierarchy:
    """
    Multi-tier memory system with working, short-term, and long-term memory.

    Memory tiers:
    1. Working Memory: Current conversation context (fast, volatile)
    2. Short-Term Memory: Recent session cache with TTL (fast, temporary)
    3. Long-Term Memory: Persistent Neo4j storage (slower, permanent)
    """

    def __init__(
        self,
        memory_db,
        working_memory_size: int = 20,
        short_term_cache_size: int = 200,
        short_term_ttl: int = 3600,
        similarity_threshold: float = 0.65
    ):
        """
        Initialize memory hierarchy.

        Args:
            memory_db: Long-term memory database (Neo4j)
            working_memory_size: Max entries in working memory
            short_term_cache_size: Max entries per user in short-term cache
            short_term_ttl: Time to live for short-term cache (seconds)
            similarity_threshold: Minimum similarity score for memories
        """
        self.long_term_memory = memory_db
        self.working_memory = WorkingMemory(max_entries=working_memory_size)
        self.short_term_memory = ShortTermMemory(
            cache_size=short_term_cache_size,
            ttl_seconds=short_term_ttl
        )
        self.similarity_threshold = similarity_threshold
        logging.info(
            f"Memory hierarchy initialized: "
            f"working={working_memory_size}, "
            f"short_term={short_term_cache_size} (TTL={short_term_ttl}s)"
        )

    async def retrieve_memories(
        self,
        query: str,
        user_id: str,
        top_k: int = 5,
        context_k: int = 20,
        include_working: bool = True
    ) -> List[Dict]:
        """
        Retrieve memories from all tiers.

        Priority order:
        1. Working memory (current conversation)
        2. Short-term memory (cached recent queries)
        3. Long-term memory (Neo4j database)

        Args:
            query: Search query
            user_id: User identifier
            top_k: Number of results to return
            context_k: Context pool size for long-term search
            include_working: Include working memory in results

        Returns:
            List of memory dictionaries with relevance scores
        """
        all_memories = []

        # 1. Check working memory (highest priority)
        if include_working:
            working_mems = self.working_memory.get_all()
            for mem in working_mems:
                mem_dict = mem.to_dict()
                mem_dict['tier'] = 'working'
                all_memories.append(mem_dict)

            if working_mems:
                logging.info(f"Retrieved {len(working_mems)} memories from working memory")

        # 2. Check short-term memory cache
        cached_memories = self.short_term_memory.get(user_id, query)
        if cached_memories:
            for mem in cached_memories:
                mem['tier'] = 'short_term'
                all_memories.append(mem)
            logging.info(f"Cache hit: Retrieved {len(cached_memories)} memories from short-term cache")
        else:
            # 3. Query long-term memory (Neo4j)
            if self.long_term_memory and self.long_term_memory.get_total_entries(user_id) > 0:
                try:
                    lt_memories = self.long_term_memory.search_with_context(
                        query=query,
                        top_k=top_k,
                        context_k=context_k
                    )

                    # Format long-term memories
                    for mem in lt_memories:
                        mem_obj = mem.get('memory')
                        mem_dict = {
                            'text': self._extract_memory_text(mem_obj),
                            'type': mem.get('type', 'vector'),
                            'score': mem.get('score', 0.0),
                            'tier': 'long_term'
                        }
                        all_memories.append(mem_dict)

                    # Cache the results
                    if lt_memories:
                        self.short_term_memory.add(user_id, query, lt_memories)
                        logging.info(f"Retrieved {len(lt_memories)} memories from long-term storage")

                except Exception as e:
                    logging.error(f"Error retrieving from long-term memory: {e}", exc_info=True)

        # Apply decay weighting based on tier
        weighted_memories = self._apply_tier_weights(all_memories)

        # Filter by threshold and sort by score
        filtered = [
            m for m in weighted_memories
            if m.get('score', 0.0) >= self.similarity_threshold or m.get('tier') == 'working'
        ]
        filtered.sort(key=lambda x: x.get('score', 0.0), reverse=True)

        # Return top_k results
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

    def _apply_tier_weights(self, memories: List[Dict]) -> List[Dict]:
        """
        Apply decay weights based on memory tier.

        Weighting:
        - Working memory: 1.0 (highest relevance)
        - Short-term memory: 0.9
        - Long-term memory: 0.8
        """
        tier_weights = {
            'working': 1.0,
            'short_term': 0.9,
            'long_term': 0.8
        }

        for mem in memories:
            tier = mem.get('tier', 'long_term')
            weight = tier_weights.get(tier, 0.8)
            original_score = mem.get('score', 0.5)
            mem['score'] = original_score * weight
            mem['original_score'] = original_score
            mem['tier_weight'] = weight

        return memories

    def add_to_working_memory(self, content: str, memory_type: str, user_id: str, importance: float = 0.5):
        """Add memory to working memory."""
        entry = MemoryEntry(
            content=content,
            memory_type=memory_type,
            user_id=user_id,
            importance=importance
        )
        self.working_memory.add(entry)

    def clear_working_memory(self):
        """Clear working memory (e.g., at end of conversation)."""
        self.working_memory.clear()
        logging.info("Working memory cleared")

    def clear_short_term_cache(self, user_id: Optional[str] = None):
        """Clear short-term cache for user or all users."""
        if user_id:
            self.short_term_memory.clear_user(user_id)
            logging.info(f"Short-term cache cleared for user: {user_id}")
        else:
            self.short_term_memory.clear_all()
            logging.info("All short-term caches cleared")

    def get_stats(self, user_id: Optional[str] = None) -> Dict:
        """Get memory hierarchy statistics."""
        stats = {
            'working_memory_count': self.working_memory.get_count(),
            'long_term_total': self.long_term_memory.get_total_entries(user_id) if self.long_term_memory else 0
        }
        return stats
