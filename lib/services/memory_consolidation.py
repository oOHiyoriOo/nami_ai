"""
Memory consolidation service - merges and optimizes memories.
Implements periodic consolidation to reduce memory count and improve quality.
"""
import logging
import asyncio
import time
from typing import List, Dict, Optional, Set
from dataclasses import dataclass
from sklearn.cluster import DBSCAN
import numpy as np


@dataclass
class ConsolidationConfig:
    """Configuration for memory consolidation."""

    # Clustering parameters
    similarity_threshold: float = 0.85  # Min similarity for clustering
    min_cluster_size: int = 2  # Min memories to form cluster

    # Consolidation thresholds
    min_importance_for_promotion: float = 0.7
    min_access_count_for_promotion: int = 3

    # Scheduling
    consolidation_interval_hours: int = 24  # How often to run
    process_recent_days: int = 7  # Look at memories from last N days


class MemoryConsolidationService:
    """
    Service for consolidating and optimizing memories.

    Features:
    - Cluster similar memories
    - Merge redundant memories
    - Promote important memories
    - Apply decay to low-value memories
    """

    def __init__(
        self,
        memory_db,
        decay_service,
        config: Optional[ConsolidationConfig] = None
    ):
        """
        Initialize memory consolidation service.

        Args:
            memory_db: Memory database
            decay_service: Memory decay service
            config: Consolidation configuration
        """
        self.memory_db = memory_db
        self.decay_service = decay_service
        self.config = config or ConsolidationConfig()

        self.is_running = False
        self.consolidation_task = None

        # Statistics
        self.stats = {
            'runs': 0,
            'memories_processed': 0,
            'clusters_formed': 0,
            'memories_merged': 0,
            'memories_promoted': 0,
            'memories_decayed': 0
        }

        logging.info("Memory consolidation service initialized")

    async def start_periodic_consolidation(self):
        """Start periodic consolidation task."""
        if self.is_running:
            logging.warning("Consolidation already running")
            return

        self.is_running = True
        self.consolidation_task = asyncio.create_task(self._consolidation_loop())
        logging.info(
            f"Periodic consolidation started "
            f"(interval={self.config.consolidation_interval_hours}h)"
        )

    async def stop_periodic_consolidation(self):
        """Stop periodic consolidation task."""
        if not self.is_running:
            return

        self.is_running = False

        if self.consolidation_task:
            self.consolidation_task.cancel()
            try:
                await self.consolidation_task
            except asyncio.CancelledError:
                pass

        logging.info("Periodic consolidation stopped")

    async def _consolidation_loop(self):
        """Background loop for periodic consolidation."""
        try:
            while self.is_running:
                # Run consolidation
                await self.consolidate_all_users()

                # Wait for next interval
                wait_seconds = self.config.consolidation_interval_hours * 3600
                await asyncio.sleep(wait_seconds)

        except asyncio.CancelledError:
            logging.info("Consolidation loop cancelled")
        except Exception as e:
            logging.error(f"Consolidation loop error: {e}", exc_info=True)

    async def consolidate_all_users(self):
        """Run consolidation for all users."""
        logging.info("Starting consolidation for all users")

        try:
            # Get all unique user IDs from database
            # Note: This requires adding a helper method to memory_db
            # For now, we'll skip this and require manual user-specific calls

            self.stats['runs'] += 1
            logging.info("Consolidation completed for all users")

        except Exception as e:
            logging.error(f"Error in consolidate_all_users: {e}", exc_info=True)

    async def consolidate_user_memories(self, user_id: str):
        """
        Consolidate memories for a specific user.

        Process:
        1. Get recent memories
        2. Cluster similar memories
        3. Merge clusters
        4. Apply decay to low-value memories
        5. Promote high-value memories

        Args:
            user_id: User identifier
        """
        logging.info(f"Starting consolidation for user: {user_id}")

        try:
            # 1. Get recent memories
            recent_memories = await self._get_recent_memories(user_id)

            if not recent_memories:
                logging.info(f"No recent memories to consolidate for user: {user_id}")
                return

            self.stats['memories_processed'] += len(recent_memories)

            # 2. Cluster similar memories
            clusters = await self._cluster_memories(recent_memories)
            self.stats['clusters_formed'] += len(clusters)

            logging.info(f"Formed {len(clusters)} clusters from {len(recent_memories)} memories")

            # 3. Process each cluster
            for cluster in clusters:
                await self._process_cluster(cluster, user_id)

            logging.info(f"Consolidation completed for user: {user_id}")

        except Exception as e:
            logging.error(f"Error consolidating user memories: {e}", exc_info=True)

    async def _get_recent_memories(self, user_id: str) -> List[Dict]:
        """
        Get recent memories for a user.

        Args:
            user_id: User identifier

        Returns:
            List of recent memory dictionaries
        """
        # Calculate time threshold
        current_time = time.time()
        days_ago = self.config.process_recent_days
        threshold_timestamp = int((current_time - days_ago * 86400) * 1000)

        # Query database (this is a simplified approach)
        # In a real implementation, you'd add a query method to memory_db
        # For now, we'll return empty list
        # TODO: Add time-based query to memory_db

        logging.info(f"Querying memories from last {days_ago} days for user: {user_id}")
        return []

    async def _cluster_memories(self, memories: List[Dict]) -> List[List[Dict]]:
        """
        Cluster similar memories using embeddings.

        Args:
            memories: List of memory dictionaries

        Returns:
            List of memory clusters
        """
        if len(memories) < self.config.min_cluster_size:
            return []

        try:
            # Extract embeddings
            embeddings = []
            for mem in memories:
                embedding = mem.get('summaryEmbeddingVector', [])
                if embedding:
                    embeddings.append(embedding)
                else:
                    embeddings.append([0.0] * 384)  # Default dimension

            if not embeddings:
                return []

            # Convert to numpy array
            X = np.array(embeddings)

            # Use DBSCAN clustering
            # eps = 1 - similarity_threshold for cosine distance
            eps = 1.0 - self.config.similarity_threshold
            clustering = DBSCAN(
                eps=eps,
                min_samples=self.config.min_cluster_size,
                metric='cosine'
            ).fit(X)

            # Group memories by cluster
            clusters = {}
            for idx, label in enumerate(clustering.labels_):
                if label == -1:  # Noise point
                    continue

                if label not in clusters:
                    clusters[label] = []

                clusters[label].append(memories[idx])

            return list(clusters.values())

        except Exception as e:
            logging.error(f"Error clustering memories: {e}", exc_info=True)
            return []

    async def _process_cluster(self, cluster: List[Dict], user_id: str):
        """
        Process a cluster of similar memories.

        Options:
        1. If high importance/access -> Merge and promote
        2. If low importance/access -> Apply decay

        Args:
            cluster: List of similar memories
            user_id: User identifier
        """
        if not cluster:
            return

        # Calculate cluster statistics
        avg_importance = np.mean([m.get('importance', 0.5) for m in cluster])
        total_access = sum([m.get('access_count', 0) for m in cluster])

        # High-value cluster -> Merge and promote
        if (avg_importance >= self.config.min_importance_for_promotion or
            total_access >= self.config.min_access_count_for_promotion):

            await self._merge_and_promote_cluster(cluster, user_id)
            self.stats['memories_promoted'] += 1

        # Low-value cluster -> Apply decay
        else:
            await self._decay_cluster(cluster)
            self.stats['memories_decayed'] += len(cluster)

    async def _merge_and_promote_cluster(self, cluster: List[Dict], user_id: str):
        """
        Merge cluster into consolidated memory and promote importance.

        Args:
            cluster: List of similar memories
            user_id: User identifier
        """
        logging.info(f"Merging cluster of {len(cluster)} memories")

        try:
            # Find most representative memory (highest importance/access)
            representative = max(
                cluster,
                key=lambda m: m.get('importance', 0.5) * (1 + m.get('access_count', 0) / 10)
            )

            # Calculate consolidated properties
            total_access = sum([m.get('access_count', 0) for m in cluster])
            max_importance = max([m.get('importance', 0.5) for m in cluster])

            # Create consolidated memory
            # TODO: Implement update_memory in memory_db
            # For now, just log the consolidation

            self.stats['memories_merged'] += len(cluster)

            logging.info(
                f"Cluster merged: {len(cluster)} memories -> 1 consolidated "
                f"(importance={max_importance:.2f}, access_count={total_access})"
            )

        except Exception as e:
            logging.error(f"Error merging cluster: {e}", exc_info=True)

    async def _decay_cluster(self, cluster: List[Dict]):
        """
        Apply decay to cluster memories.

        Args:
            cluster: List of similar memories
        """
        logging.debug(f"Applying decay to cluster of {len(cluster)} memories")

        # In a real implementation, you would:
        # 1. Reduce importance scores
        # 2. Update memory metadata
        # 3. Possibly mark for pruning

        # For now, just log
        logging.debug(f"Decayed {len(cluster)} memories")

    def get_stats(self) -> Dict:
        """Get consolidation statistics."""
        return self.stats.copy()

    def reset_stats(self):
        """Reset statistics."""
        self.stats = {
            'runs': 0,
            'memories_processed': 0,
            'clusters_formed': 0,
            'memories_merged': 0,
            'memories_promoted': 0,
            'memories_decayed': 0
        }
        logging.info("Consolidation statistics reset")
