"""
Memory consolidation service - merges and optimizes memories.
Implements periodic consolidation to reduce memory count and improve quality.
"""
import logging
import asyncio
import time
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

    # Embedding settings
    embedding_dimension: int = 384  # Default embedding dimension


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
        config: ConsolidationConfig | None = None
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
        """Run consolidation for all users who have stored memories."""
        logging.info("Starting consolidation for all users")

        try:
            driver = self.memory_db.get_driver()
            async with driver.session() as session:
                result = await session.run(
                    "MATCH (u:Person)-[:IS_AUTHOR_OF]->() RETURN DISTINCT u.id AS user_id"
                )
                user_ids = [record["user_id"] async for record in result]

            logging.info(f"Found {len(user_ids)} users to consolidate")
            for user_id in user_ids:
                await self.consolidate_user_memories(user_id)

            self.stats['runs'] += 1
            logging.info(f"Consolidation completed for {len(user_ids)} users")

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

    async def _get_recent_memories(self, user_id: str) -> list[dict]:
        """
        Get recent memories for a user from the last N days.

        Args:
            user_id: User identifier (must match Person node id in Neo4j).

        Returns:
            List of recent memory dictionaries with id, type, embedding, importance, access_count.
        """
        current_time = time.time()
        days_ago = self.config.process_recent_days
        threshold_timestamp = int((current_time - days_ago * 86400) * 1000)

        logging.info(f"Querying memories from last {days_ago} days for user: {user_id}")

        try:
            driver = self.memory_db.get_driver()
            async with driver.session() as session:
                query = """
                MATCH (u:Person {id: $user_id})-[:IS_AUTHOR_OF]->(m)
                WHERE (m:EpisodicMemory OR m:KnowledgeUnit OR m:ProceduralUnit)
                  AND toFloat(m.creationTimestamp) >= $threshold
                RETURN m.id as id,
                       labels(m)[0] as memory_type,
                       m.summaryEmbeddingVector as embedding,
                       COALESCE(m.importance, 0.5) as importance,
                       COALESCE(m.access_count, 0) as access_count,
                       m.creationTimestamp as creation_timestamp,
                       COALESCE(m.summary, m.statement, m.description) as content
                ORDER BY m.creationTimestamp DESC
                """

                result = await session.run(
                    query,
                    threshold=threshold_timestamp,
                    user_id=str(user_id)
                )

                memories = []
                async for record in result:
                    memories.append({
                        'id': record['id'],
                        'memory_type': record['memory_type'],
                        'summaryEmbeddingVector': record['embedding'] or [],
                        'importance': record['importance'],
                        'access_count': record['access_count'],
                        'creation_timestamp': record['creation_timestamp'],
                        'content': record['content']
                    })

                logging.info(f"Found {len(memories)} recent memories for user: {user_id}")
                return memories

        except Exception as e:
            logging.error(f"Error querying recent memories: {e}", exc_info=True)
            return []

    async def _cluster_memories(self, memories: list[dict]) -> list[list[dict]]:
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
                    embeddings.append([0.0] * self.config.embedding_dimension)

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

    async def _process_cluster(self, cluster: list[dict], user_id: str):
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

    async def _merge_and_promote_cluster(self, cluster: list[dict], user_id: str):
        """
        Merge cluster into consolidated memory and promote importance.

        Keeps the most representative memory (highest score) and merges others into it.

        Args:
            cluster: List of similar memories
            user_id: User identifier
        """
        if len(cluster) < 2:
            return

        logging.info(f"Merging cluster of {len(cluster)} memories")

        try:
            # Find most representative memory (highest importance * access score)
            representative = max(
                cluster,
                key=lambda m: m.get('importance', 0.5) * (1 + m.get('access_count', 0) / 10)
            )

            # Source memories are all except the representative
            source_ids = [m['id'] for m in cluster if m['id'] != representative['id']]
            target_id = representative['id']
            memory_type = representative.get('memory_type', 'EpisodicMemory')

            if not source_ids:
                return

            # Calculate consolidated properties
            total_access = sum(m.get('access_count', 0) for m in cluster)
            max_importance = max(m.get('importance', 0.5) for m in cluster)

            # Boost importance slightly for consolidated memories (they're reinforced)
            boosted_importance = min(1.0, max_importance * 1.1)

            driver = self.memory_db.get_driver()
            async with driver.session() as session:
                # Update target memory with merged values
                update_query = f"""
                MATCH (target:{memory_type} {{id: $target_id}})
                SET target.access_count = $total_access,
                    target.importance = $importance,
                    target.mergedFromCount = COALESCE(target.mergedFromCount, 0) + $source_count,
                    target.lastConsolidatedTimestamp = timestamp()
                RETURN target.id
                """

                await session.run(
                    update_query,
                    target_id=target_id,
                    total_access=total_access,
                    importance=boosted_importance,
                    source_count=len(source_ids)
                )

                # Transfer any relationships from sources to target
                rel_query = f"""
                MATCH (source:{memory_type})-[r]->(related)
                WHERE source.id IN $source_ids AND NOT related.id = $target_id
                WITH source, related, type(r) as relType, properties(r) as relProps
                MATCH (target:{memory_type} {{id: $target_id}})
                MERGE (target)-[new_r:CONSOLIDATED_FROM]->(related)
                SET new_r.originalType = relType
                """

                await session.run(rel_query, source_ids=source_ids, target_id=target_id)

                # Delete source memories (they're now merged)
                delete_query = f"""
                MATCH (m:{memory_type})
                WHERE m.id IN $source_ids
                DETACH DELETE m
                """

                await session.run(delete_query, source_ids=source_ids)

            self.stats['memories_merged'] += len(source_ids)

            logging.info(
                f"Cluster merged: {len(cluster)} memories -> 1 consolidated "
                f"(id={target_id}, importance={boosted_importance:.2f}, access_count={total_access})"
            )

        except Exception as e:
            logging.error(f"Error merging cluster: {e}", exc_info=True)

    async def _decay_cluster(self, cluster: list[dict]):
        """
        Apply decay to cluster memories - reduce importance scores.

        For low-value clusters, we reduce importance to make them candidates for pruning.

        Args:
            cluster: List of similar memories
        """
        if not cluster:
            return

        logging.debug(f"Applying decay to cluster of {len(cluster)} memories")

        try:
            driver = self.memory_db.get_driver()
            
            for memory in cluster:
                memory_id = memory.get('id')
                memory_type = memory.get('memory_type', 'EpisodicMemory')
                current_importance = memory.get('importance', 0.5)
                creation_timestamp = memory.get('creation_timestamp', 0)

                # Use decay service to compute decay factor
                decay_factor = self.decay_service.compute_decay_factor(creation_timestamp)
                
                # Apply decay to importance (reduce by decay factor, minimum 0.1)
                new_importance = max(0.1, current_importance * decay_factor)

                async with driver.session() as session:
                    update_query = f"""
                    MATCH (m:{memory_type} {{id: $memory_id}})
                    SET m.importance = $new_importance,
                        m.lastDecayTimestamp = timestamp()
                    """

                    await session.run(
                        update_query,
                        memory_id=memory_id,
                        new_importance=new_importance
                    )

            logging.debug(f"Decayed {len(cluster)} memories")

        except Exception as e:
            logging.error(f"Error decaying cluster: {e}", exc_info=True)

    def get_stats(self) -> dict:
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
