"""
Memory hierarchy service - implements multi-tier memory system.
Provides working memory (current context), short-term memory (session cache),
and long-term memory (Neo4j persistent storage).
"""
import asyncio
import logging
import time
from typing import Any
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


class TTLCache:
    """Time-To-Live cache with automatic expiration. Thread-safe for async contexts."""

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
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        """Get value from cache if not expired."""
        async with self._lock:
            if key not in self.cache:
                return None

            # Check if expired
            if time.time() - self.timestamps[key] > self.ttl:
                self._remove_unlocked(key)
                return None

            # Move to end (most recently used)
            self.cache.move_to_end(key)
            return self.cache[key]

    async def set(self, key: str, value: Any):
        """Set value in cache."""
        async with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            else:
                if len(self.cache) >= self.maxsize:
                    # Remove oldest item
                    oldest_key = next(iter(self.cache))
                    self._remove_unlocked(oldest_key)

            self.cache[key] = value
            self.timestamps[key] = time.time()

    def _remove_unlocked(self, key: str):
        """Remove key from cache (must hold lock)."""
        if key in self.cache:
            del self.cache[key]
            del self.timestamps[key]

    async def remove(self, key: str):
        """Remove key from cache."""
        async with self._lock:
            self._remove_unlocked(key)

    async def clear(self):
        """Clear all entries."""
        async with self._lock:
            self.cache.clear()
            self.timestamps.clear()

    async def items(self):
        """Get all items."""
        async with self._lock:
            return list(self.cache.items())


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

    def get_all(self) -> list[MemoryEntry]:
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

    async def add(self, user_id: str, query: str, memories: list[dict]):
        """Cache memories for a query."""
        cache = self._get_user_cache(user_id)
        await cache.set(query, memories)

    async def get(self, user_id: str, query: str) -> list[dict] | None:
        """Get cached memories for a query."""
        cache = self._get_user_cache(user_id)
        return await cache.get(query)

    async def clear_user(self, user_id: str):
        """Clear cache for specific user."""
        if user_id in self.user_caches:
            await self.user_caches[user_id].clear()

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
    ) -> list[dict]:
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

        # 2. Check short-term memory cache (global — shared across all users)
        cached_memories = await self.short_term_memory.get("global", query)
        if cached_memories:
            for mem in cached_memories:
                # If already formatted with a text field, use as-is; otherwise
                # extract text from the raw _vector_search() result (mem['memory'])
                if 'text' not in mem:
                    mem_obj = mem.get('memory')
                    mem_id = getattr(mem_obj, 'id', None) if not isinstance(mem_obj, dict) else mem_obj.get('id')
                    mem_type = type(mem_obj).__name__ if not isinstance(mem_obj, dict) else None
                    mem['text'] = self._extract_memory_text(mem_obj)
                    mem['memory_id'] = mem_id
                    mem['memory_type'] = mem_type
                mem['tier'] = 'short_term'
                all_memories.append(mem)
            logging.info(f"Cache hit: Retrieved {len(cached_memories)} memories from short-term cache")
        else:
            # 3. Query long-term memory (Neo4j)
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
                        mem_labels = list(getattr(mem_obj, '__class__', object).__mro__)
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

                    # Cache the results (global — shared across all users)
                    if lt_memories:
                        await self.short_term_memory.add("global", query, lt_memories)
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

    def _apply_tier_weights(self, memories: list[dict]) -> list[dict]:
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

    async def clear_short_term_cache(self, user_id: str | None = None):
        """Clear the global short-term cache.

        user_id is accepted for backward compatibility but the cache is
        now global — all users share the same query cache.
        """
        await self.short_term_memory.clear_user("global")
        logging.info("Global short-term cache cleared")

    async def get_stats(self, user_id: str | None = None) -> dict:
        """
        Get memory hierarchy statistics.

        Returns working memory count and long-term total.
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
            "working_memory_count": self.working_memory.get_count(),
            "long_term_total": long_term_total,
        }
