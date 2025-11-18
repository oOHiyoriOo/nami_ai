"""
Background memory writer - async memory storage pipeline.
Processes and stores memories in the background without blocking conversation flow.
"""
import logging
import asyncio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime
import time


@dataclass
class PendingMemory:
    """Represents a memory pending background processing."""

    user_id: str
    user_name: str
    memory_type: str
    memory_args: Dict[str, Any]
    timestamp: float = None
    conversation_id: Optional[str] = None
    priority: int = 0  # Higher = process first

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()


class MemoryValidator:
    """Validates and deduplicates memories before storage."""

    def __init__(self, memory_db):
        """
        Initialize memory validator.

        Args:
            memory_db: Memory database for checking duplicates
        """
        self.memory_db = memory_db

    def validate(self, memory: PendingMemory) -> tuple[bool, Optional[str]]:
        """
        Validate memory before storage.

        Checks:
        - Required fields present
        - Content not empty
        - Memory type valid

        Args:
            memory: Pending memory to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check memory type
        valid_types = ["EpisodicMemory", "KnowledgeUnit", "ProceduralUnit", ""]
        if memory.memory_type not in valid_types:
            return False, f"Invalid memory type: {memory.memory_type}"

        # Skip empty memories
        if memory.memory_type == "":
            return False, "Empty memory type (intentional skip)"

        # Check required fields based on type
        if memory.memory_type == "EpisodicMemory":
            if not memory.memory_args.get('summary'):
                return False, "EpisodicMemory missing 'summary' field"

        elif memory.memory_type == "KnowledgeUnit":
            if not memory.memory_args.get('statement'):
                return False, "KnowledgeUnit missing 'statement' field"

        elif memory.memory_type == "ProceduralUnit":
            if not (memory.memory_args.get('name') or memory.memory_args.get('description')):
                return False, "ProceduralUnit missing 'name' or 'description' field"

        # Check user ID
        if not memory.user_id:
            return False, "Missing user_id"

        return True, None

    async def check_duplicate(self, memory: PendingMemory, similarity_threshold: float = 0.95) -> bool:
        """
        Check if memory is a duplicate of existing memory.

        Args:
            memory: Memory to check
            similarity_threshold: Threshold for considering duplicate

        Returns:
            True if duplicate exists
        """
        if not self.memory_db:
            return False

        try:
            # Extract searchable text
            text = self._get_memory_text(memory)
            if not text:
                return False

            # Search for similar memories
            results = self.memory_db.search(query=text, user_id=memory.user_id, top_k=3)

            # Check if any result is very similar
            for result in results:
                if len(result) >= 2:
                    similarity = result[1]
                    if similarity >= similarity_threshold:
                        logging.info(f"Duplicate memory detected (similarity={similarity:.3f})")
                        return True

            return False

        except Exception as e:
            logging.error(f"Error checking duplicate: {e}", exc_info=True)
            return False

    def _get_memory_text(self, memory: PendingMemory) -> str:
        """Extract searchable text from memory."""
        if memory.memory_type == "EpisodicMemory":
            return memory.memory_args.get('summary', '')
        elif memory.memory_type == "KnowledgeUnit":
            return memory.memory_args.get('statement', '')
        elif memory.memory_type == "ProceduralUnit":
            return memory.memory_args.get('description', '') or memory.memory_args.get('name', '')
        return ''


class BackgroundMemoryWriter:
    """
    Background memory writer with async processing queue.

    Features:
    - Non-blocking memory storage
    - Validation and deduplication
    - Batch processing
    - Priority queue
    - Error handling and retry
    """

    def __init__(
        self,
        memory_db,
        batch_size: int = 10,
        batch_interval: float = 5.0,
        max_retries: int = 3,
        enable_deduplication: bool = True
    ):
        """
        Initialize background memory writer.

        Args:
            memory_db: Memory database for storage
            batch_size: Number of memories to process in batch
            batch_interval: Seconds between batch processing
            max_retries: Maximum retry attempts for failed writes
            enable_deduplication: Enable duplicate detection
        """
        self.memory_db = memory_db
        self.batch_size = batch_size
        self.batch_interval = batch_interval
        self.max_retries = max_retries
        self.enable_deduplication = enable_deduplication

        self.queue = asyncio.PriorityQueue()
        self.validator = MemoryValidator(memory_db)
        self.worker_task = None
        self.is_running = False

        # Statistics
        self.stats = {
            'queued': 0,
            'processed': 0,
            'validated': 0,
            'duplicates_skipped': 0,
            'errors': 0,
            'retries': 0
        }

        logging.info(
            f"Background memory writer initialized: "
            f"batch_size={batch_size}, interval={batch_interval}s"
        )

    async def queue_memory(
        self,
        user_id: str,
        user_name: str,
        memory_type: str,
        memory_args: Dict[str, Any],
        conversation_id: Optional[str] = None,
        priority: int = 0
    ):
        """
        Queue memory for background processing.

        Non-blocking operation that returns immediately.

        Args:
            user_id: User identifier
            user_name: User display name
            memory_type: Type of memory (EpisodicMemory, KnowledgeUnit, ProceduralUnit)
            memory_args: Memory properties
            conversation_id: Optional conversation identifier
            priority: Priority (higher = process first)
        """
        memory = PendingMemory(
            user_id=user_id,
            user_name=user_name,
            memory_type=memory_type,
            memory_args=memory_args,
            conversation_id=conversation_id,
            priority=priority
        )

        # Add to queue (negative priority for max-heap behavior)
        await self.queue.put((-priority, memory))
        self.stats['queued'] += 1

        logging.debug(f"Memory queued: type={memory_type}, user={user_id}, priority={priority}")

    async def start(self):
        """Start background worker."""
        if self.is_running:
            logging.warning("Background worker already running")
            return

        self.is_running = True
        self.worker_task = asyncio.create_task(self._background_worker())
        logging.info("Background memory writer started")

    async def stop(self):
        """Stop background worker and process remaining queue."""
        if not self.is_running:
            return

        self.is_running = False

        # Process remaining items
        await self._process_queue()

        # Cancel worker task
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass

        logging.info("Background memory writer stopped")

    async def _background_worker(self):
        """Background worker loop."""
        try:
            while self.is_running:
                # Process queue periodically
                await self._process_queue()

                # Wait before next batch
                await asyncio.sleep(self.batch_interval)

        except asyncio.CancelledError:
            logging.info("Background worker cancelled")
        except Exception as e:
            logging.error(f"Background worker error: {e}", exc_info=True)
            self.stats['errors'] += 1

    async def _process_queue(self):
        """Process queued memories in batch."""
        batch = []

        # Collect batch
        while len(batch) < self.batch_size and not self.queue.empty():
            try:
                priority, memory = await asyncio.wait_for(
                    self.queue.get(),
                    timeout=0.1
                )
                batch.append(memory)
            except asyncio.TimeoutError:
                break

        if not batch:
            return

        logging.info(f"Processing batch of {len(batch)} memories")

        # Process each memory
        for memory in batch:
            await self._process_memory(memory)

    async def _process_memory(self, memory: PendingMemory, retry_count: int = 0):
        """
        Process and store a single memory.

        Args:
            memory: Memory to process
            retry_count: Current retry attempt
        """
        try:
            # 1. Validate
            is_valid, error = self.validator.validate(memory)
            if not is_valid:
                logging.debug(f"Memory validation failed: {error}")
                return

            self.stats['validated'] += 1

            # 2. Check duplicates
            if self.enable_deduplication:
                is_duplicate = await self.validator.check_duplicate(memory)
                if is_duplicate:
                    self.stats['duplicates_skipped'] += 1
                    logging.debug(f"Skipping duplicate memory: {memory.memory_type}")
                    return

            # 3. Store in database
            self.memory_db.add_memory(
                user_id=memory.user_id,
                user_name=memory.user_name,
                memory_type=memory.memory_type,
                memory_args=memory.memory_args
            )

            self.stats['processed'] += 1
            logging.info(
                f"Memory stored: type={memory.memory_type}, user={memory.user_id}"
            )

        except Exception as e:
            self.stats['errors'] += 1
            logging.error(f"Error processing memory: {e}", exc_info=True)

            # Retry if under limit
            if retry_count < self.max_retries:
                self.stats['retries'] += 1
                logging.info(f"Retrying memory storage (attempt {retry_count + 1}/{self.max_retries})")
                await asyncio.sleep(1.0 * (retry_count + 1))  # Exponential backoff
                await self._process_memory(memory, retry_count + 1)
            else:
                logging.error(f"Memory storage failed after {self.max_retries} retries")

    def get_queue_size(self) -> int:
        """Get current queue size."""
        return self.queue.qsize()

    def get_stats(self) -> Dict[str, int]:
        """Get processing statistics."""
        return {
            **self.stats,
            'queue_size': self.get_queue_size()
        }

    def reset_stats(self):
        """Reset statistics counters."""
        self.stats = {
            'queued': 0,
            'processed': 0,
            'validated': 0,
            'duplicates_skipped': 0,
            'errors': 0,
            'retries': 0
        }
        logging.info("Statistics reset")
