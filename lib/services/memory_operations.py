"""
Memory operations service - provides update, merge, and pruning operations.
Extends memory_db with additional CRUD and maintenance operations.
"""
import logging
import asyncio
import time
from typing import List, Dict, Optional, Any
from neo4j import GraphDatabase


class MemoryOperations:
    """
    Advanced memory operations for Neo4j memory database.

    Provides:
    - Update existing memories
    - Merge duplicate memories
    - Prune old/low-value memories
    - Batch operations
    """

    def __init__(self, memory_db, decay_service=None):
        """
        Initialize memory operations.

        Args:
            memory_db: MemoryDb instance
            decay_service: Optional MemoryDecayService for pruning decisions
        """
        self.memory_db = memory_db
        self.decay_service = decay_service

        # Statistics
        self.stats = {
            'updates': 0,
            'merges': 0,
            'prunes': 0,
            'errors': 0
        }

        logging.info("Memory operations service initialized")

    def update_memory(
        self,
        memory_id: str,
        memory_type: str,
        updates: Dict[str, Any]
    ) -> bool:
        """
        Update an existing memory node.

        Args:
            memory_id: Memory node ID
            memory_type: Type of memory (EpisodicMemory, KnowledgeUnit, ProceduralUnit)
            updates: Dictionary of fields to update

        Returns:
            True if successful
        """
        try:
            driver = self.memory_db.get_driver()
            with driver.session() as session:
                # Build SET clause from updates
                set_clauses = []
                for key, value in updates.items():
                    if isinstance(value, str):
                        set_clauses.append(f'm.{key} = "{value}"')
                    elif isinstance(value, (int, float)):
                        set_clauses.append(f'm.{key} = {value}')
                    elif isinstance(value, list):
                        set_clauses.append(f'm.{key} = {value}')

                # Add lastModified timestamp
                set_clauses.append('m.lastModifiedTimestamp = timestamp()')

                set_clause = ', '.join(set_clauses)

                query = f"""
                MATCH (m:{memory_type} {{id: $memory_id}})
                SET {set_clause}
                RETURN m
                """

                result = session.run(query, memory_id=memory_id)
                record = result.single()

                if record:
                    self.stats['updates'] += 1
                    logging.info(f"Memory updated: {memory_id}")
                    return True
                else:
                    logging.warning(f"Memory not found: {memory_id}")
                    return False

        except Exception as e:
            self.stats['errors'] += 1
            logging.error(f"Error updating memory: {e}", exc_info=True)
            return False

    def increment_access_count(self, memory_id: str, memory_type: str) -> bool:
        """
        Increment access count for a memory.

        Args:
            memory_id: Memory node ID
            memory_type: Type of memory

        Returns:
            True if successful
        """
        try:
            driver = self.memory_db.get_driver()
            with driver.session() as session:
                query = f"""
                MATCH (m:{memory_type} {{id: $memory_id}})
                SET m.access_count = COALESCE(m.access_count, 0) + 1
                SET m.lastAccessedTimestamp = timestamp()
                RETURN m.access_count as count
                """

                result = session.run(query, memory_id=memory_id)
                record = result.single()

                if record:
                    count = record['count']
                    logging.debug(f"Access count incremented: {memory_id} -> {count}")
                    return True
                else:
                    return False

        except Exception as e:
            logging.error(f"Error incrementing access count: {e}", exc_info=True)
            return False

    def merge_memories(
        self,
        source_ids: List[str],
        target_id: str,
        memory_type: str
    ) -> bool:
        """
        Merge multiple memories into a single target memory.

        Combines:
        - Access counts (sum)
        - Importance scores (max)
        - Relationships (transfer to target)

        Args:
            source_ids: List of source memory IDs to merge
            target_id: Target memory ID to merge into
            memory_type: Type of memory

        Returns:
            True if successful
        """
        try:
            driver = self.memory_db.get_driver()
            with driver.session() as session:
                # Get source memories data
                query = f"""
                MATCH (m:{memory_type})
                WHERE m.id IN $source_ids
                RETURN m.id as id,
                       COALESCE(m.access_count, 0) as access_count,
                       COALESCE(m.importance, 0.5) as importance
                """

                result = session.run(query, source_ids=source_ids)
                sources = list(result)

                if not sources:
                    logging.warning(f"No source memories found for merge")
                    return False

                # Calculate merged values
                total_access = sum([s['access_count'] for s in sources])
                max_importance = max([s['importance'] for s in sources])

                # Update target memory
                update_query = f"""
                MATCH (target:{memory_type} {{id: $target_id}})
                SET target.access_count = COALESCE(target.access_count, 0) + $total_access
                SET target.importance = GREATEST(COALESCE(target.importance, 0.5), $max_importance)
                SET target.mergedFromCount = COALESCE(target.mergedFromCount, 0) + $source_count
                SET target.lastModifiedTimestamp = timestamp()
                """

                session.run(
                    update_query,
                    target_id=target_id,
                    total_access=total_access,
                    max_importance=max_importance,
                    source_count=len(sources)
                )

                # Transfer relationships from sources to target
                rel_query = f"""
                MATCH (source:{memory_type})-[r]->(related)
                WHERE source.id IN $source_ids
                WITH source, related, type(r) as relType
                MATCH (target:{memory_type} {{id: $target_id}})
                MERGE (target)-[new_r:MERGED_RELATION]->(related)
                SET new_r.originalType = relType
                """

                session.run(rel_query, source_ids=source_ids, target_id=target_id)

                # Delete source memories
                delete_query = f"""
                MATCH (m:{memory_type})
                WHERE m.id IN $source_ids
                DETACH DELETE m
                """

                session.run(delete_query, source_ids=source_ids)

                self.stats['merges'] += len(sources)
                logging.info(
                    f"Merged {len(sources)} memories into {target_id} "
                    f"(access={total_access}, importance={max_importance:.2f})"
                )
                return True

        except Exception as e:
            self.stats['errors'] += 1
            logging.error(f"Error merging memories: {e}", exc_info=True)
            return False

    async def prune_old_memories(
        self,
        user_id: Optional[str] = None,
        dry_run: bool = False
    ) -> Dict[str, int]:
        """
        Prune old, low-value memories.

        Prunes memories that are:
        - Old (> 90 days by default)
        - Low importance (< 0.3)
        - Low access count (< 2)

        Args:
            user_id: Optional user ID to prune (all users if None)
            dry_run: If True, only report what would be pruned

        Returns:
            Dictionary with pruning statistics
        """
        results = {
            'total_scanned': 0,
            'total_pruned': 0,
            'episodic_pruned': 0,
            'knowledge_pruned': 0,
            'procedural_pruned': 0
        }

        try:
            driver = self.memory_db.get_driver()
            current_time = time.time()

            for memory_type in ["EpisodicMemory", "KnowledgeUnit", "ProceduralUnit"]:
                pruned = await self._prune_memory_type(
                    driver=driver,
                    memory_type=memory_type,
                    user_id=user_id,
                    current_time=current_time,
                    dry_run=dry_run
                )

                results['total_pruned'] += pruned
                results[f'{memory_type.lower()}_pruned'] = pruned

            self.stats['prunes'] += results['total_pruned']

            logging.info(
                f"Pruning {'simulation' if dry_run else 'completed'}: "
                f"{results['total_pruned']} memories pruned"
            )

            return results

        except Exception as e:
            self.stats['errors'] += 1
            logging.error(f"Error pruning memories: {e}", exc_info=True)
            return results

    async def _prune_memory_type(
        self,
        driver,
        memory_type: str,
        user_id: Optional[str],
        current_time: float,
        dry_run: bool
    ) -> int:
        """
        Prune memories of a specific type.

        Args:
            driver: Neo4j driver
            memory_type: Type to prune
            user_id: Optional user filter
            current_time: Current timestamp
            dry_run: Simulation mode

        Returns:
            Number of memories pruned
        """
        # Use decay service if available
        if self.decay_service:
            max_age_ms = self.decay_service.config.max_age_days * 86400 * 1000
            min_access = self.decay_service.config.min_access_count
            min_importance = self.decay_service.config.min_importance
        else:
            max_age_ms = 90 * 86400 * 1000  # 90 days
            min_access = 2
            min_importance = 0.3

        threshold_timestamp = int((current_time * 1000) - max_age_ms)

        with driver.session() as session:
            # Build query
            user_filter = ""
            if user_id:
                user_filter = "AND EXISTS((u:Person {id: $user_id})-[:IST_AUTOR_VON]->(m))"

            if dry_run:
                query = f"""
                MATCH (m:{memory_type})
                WHERE COALESCE(m.access_count, 0) < $min_access
                  AND COALESCE(m.importance, 0.5) < $min_importance
                  AND m.creationTimestamp < $threshold_timestamp
                  {user_filter}
                RETURN count(m) as count
                """

                result = session.run(
                    query,
                    min_access=min_access,
                    min_importance=min_importance,
                    threshold_timestamp=threshold_timestamp,
                    user_id=user_id
                )
                record = result.single()
                return record['count'] if record else 0

            else:
                query = f"""
                MATCH (m:{memory_type})
                WHERE COALESCE(m.access_count, 0) < $min_access
                  AND COALESCE(m.importance, 0.5) < $min_importance
                  AND m.creationTimestamp < $threshold_timestamp
                  {user_filter}
                WITH m
                DETACH DELETE m
                RETURN count(*) as count
                """

                result = session.run(
                    query,
                    min_access=min_access,
                    min_importance=min_importance,
                    threshold_timestamp=threshold_timestamp,
                    user_id=user_id
                )
                record = result.single()
                return record['count'] if record else 0

    def find_similar_memories(
        self,
        memory_id: str,
        memory_type: str,
        similarity_threshold: float = 0.9,
        top_k: int = 5
    ) -> List[Dict]:
        """
        Find memories similar to a given memory.

        Args:
            memory_id: Reference memory ID
            memory_type: Type of memory
            similarity_threshold: Minimum similarity score
            top_k: Number of results to return

        Returns:
            List of similar memory dictionaries
        """
        try:
            driver = self.memory_db.get_driver()
            with driver.session() as session:
                # Get reference memory embedding
                query = f"""
                MATCH (m:{memory_type} {{id: $memory_id}})
                RETURN m.summaryEmbeddingVector as embedding
                """

                result = session.run(query, memory_id=memory_id)
                record = result.single()

                if not record or not record['embedding']:
                    logging.warning(f"Memory not found or no embedding: {memory_id}")
                    return []

                query_embedding = record['embedding']

                # Vector search
                index_name = f"{memory_type}EmbeddingIndex"
                search_query = f"""
                CALL db.index.vector.queryNodes('{index_name}', $top_k, $query_embedding)
                YIELD node, score
                WHERE node.id <> $memory_id AND score >= $threshold
                RETURN node, score
                ORDER BY score DESC
                """

                results = session.run(
                    search_query,
                    top_k=top_k,
                    query_embedding=query_embedding,
                    memory_id=memory_id,
                    threshold=similarity_threshold
                )

                similar = []
                for record in results:
                    node = record['node']
                    score = record['score']
                    similar.append({
                        'id': node.get('id'),
                        'summary': node.get('summary') or node.get('statement') or node.get('description'),
                        'score': score,
                        'type': memory_type
                    })

                logging.info(f"Found {len(similar)} similar memories for {memory_id}")
                return similar

        except Exception as e:
            logging.error(f"Error finding similar memories: {e}", exc_info=True)
            return []

    def get_stats(self) -> Dict[str, int]:
        """Get operation statistics."""
        return self.stats.copy()

    def reset_stats(self):
        """Reset statistics."""
        self.stats = {
            'updates': 0,
            'merges': 0,
            'prunes': 0,
            'errors': 0
        }
        logging.info("Memory operations statistics reset")
