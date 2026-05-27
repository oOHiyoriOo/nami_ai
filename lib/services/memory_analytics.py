"""
Memory analytics service - monitoring and diagnostics for memory system.
Provides insights into memory system health, performance, and quality.
"""
import logging
import time
import json

from lib.global_registry import g_data


# Default settings (can be overridden via config)
DEFAULT_CLEANUP_THRESHOLD = 10000


class MemoryAnalytics:
    """
    Analytics and monitoring for memory system.

    Provides:
    - System health metrics
    - Performance statistics
    - Quality indicators
    - Usage patterns
    - Diagnostic tools
    """

    def __init__(self, memory_db, memory_hierarchy=None):
        """
        Initialize memory analytics.

        Args:
            memory_db: Memory database instance
            memory_hierarchy: Optional memory hierarchy instance
        """
        self.memory_db = memory_db
        self.memory_hierarchy = memory_hierarchy

        logging.info("Memory analytics service initialized")

    def _get_cleanup_threshold(self) -> int:
        """Get cleanup threshold from config or default."""
        memory_settings = g_data.get("memory_settings") or {}
        return memory_settings.get('cleanup_threshold', DEFAULT_CLEANUP_THRESHOLD)

    async def get_system_health(self, user_id: str | None = None) -> dict:
        """
        Get overall memory system health metrics.

        Args:
            user_id: Optional user filter

        Returns:
            Dictionary with health metrics
        """
        health = {
            'timestamp': time.time(),
            'user_id': user_id,
            'database': await self._get_database_health(user_id),
            'quality': await self._get_quality_metrics(user_id),
            'status': 'healthy'
        }

        # Add hierarchy stats if available
        if self.memory_hierarchy:
            health['hierarchy'] = await self.memory_hierarchy.get_stats(user_id)

        # Determine overall status
        db_total = health['database']['total_memories']
        cleanup_threshold = self._get_cleanup_threshold()
        if db_total > cleanup_threshold:
            health['status'] = 'needs_cleanup'
        elif db_total == 0:
            health['status'] = 'empty'

        return health

    async def _get_database_health(self, user_id: str | None = None) -> dict:
        """Get database health metrics."""
        try:
            driver = self.memory_db.get_driver()
            async with driver.session() as session:
                counts = {}
                total = 0

                for memory_type in ["EpisodicMemory", "KnowledgeUnit", "ProceduralUnit"]:
                    if user_id:
                        query = f"""
                        MATCH (u:Person {{id: $user_id}})-[:IS_AUTHOR_OF]->(m:{memory_type})
                        RETURN count(m) as count
                        """
                        result = await session.run(query, user_id=user_id)
                    else:
                        query = f"""
                        MATCH (m:{memory_type})
                        RETURN count(m) as count
                        """
                        result = await session.run(query)

                    record = await result.single()
                    count = record['count'] if record else 0
                    counts[memory_type] = count
                    total += count

                return {
                    'total_memories': total,
                    'episodic_memories': counts.get('EpisodicMemory', 0),
                    'knowledge_units': counts.get('KnowledgeUnit', 0),
                    'procedural_units': counts.get('ProceduralUnit', 0)
                }

        except Exception as e:
            logging.error(f"Error getting database health: {e}", exc_info=True)
            return {
                'total_memories': 0,
                'episodic_memories': 0,
                'knowledge_units': 0,
                'procedural_units': 0,
                'error': str(e)
            }

    async def _get_quality_metrics(self, user_id: str | None = None) -> dict:
        """Get memory quality metrics."""
        try:
            driver = self.memory_db.get_driver()
            async with driver.session() as session:
                user_filter = ""
                if user_id:
                    user_filter = "MATCH (u:Person {id: $user_id})-[:IS_AUTHOR_OF]->(m)"

                query = f"""
                {user_filter}
                MATCH (m)
                WHERE m:EpisodicMemory OR m:KnowledgeUnit OR m:ProceduralUnit
                RETURN
                    avg(COALESCE(m.importance, 0.5)) as avg_importance,
                    avg(COALESCE(m.access_count, 0)) as avg_access_count,
                    avg(COALESCE(m.confidenceScore, 0.5)) as avg_confidence,
                    count(CASE WHEN COALESCE(m.access_count, 0) = 0 THEN 1 END) as unused_count,
                    count(CASE WHEN COALESCE(m.importance, 0.5) < 0.3 THEN 1 END) as low_importance_count
                """

                params = {'user_id': user_id} if user_id else {}
                result = await session.run(query, params)
                record = await result.single()

                if record:
                    return {
                        'avg_importance': round(record['avg_importance'] or 0.5, 3),
                        'avg_access_count': round(record['avg_access_count'] or 0, 2),
                        'avg_confidence': round(record['avg_confidence'] or 0.5, 3),
                        'unused_memories': record['unused_count'] or 0,
                        'low_importance_memories': record['low_importance_count'] or 0
                    }
                else:
                    return {
                        'avg_importance': 0.0,
                        'avg_access_count': 0.0,
                        'avg_confidence': 0.0,
                        'unused_memories': 0,
                        'low_importance_memories': 0
                    }

        except Exception as e:
            logging.error(f"Error getting quality metrics: {e}", exc_info=True)
            return {'error': str(e)}

    async def get_memory_age_distribution(self, user_id: str | None = None) -> dict:
        """
        Get distribution of memory ages.

        Args:
            user_id: Optional user filter

        Returns:
            Age distribution histogram
        """
        try:
            driver = self.memory_db.get_driver()
            current_time = time.time() * 1000  # Convert to milliseconds

            async with driver.session() as session:
                user_filter = ""
                if user_id:
                    user_filter = "MATCH (u:Person {id: $user_id})-[:IS_AUTHOR_OF]->(m)"

                query = f"""
                {user_filter}
                MATCH (m)
                WHERE m:EpisodicMemory OR m:KnowledgeUnit OR m:ProceduralUnit
                WITH m, toFloat(m.creationTimestamp) AS ts
                WHERE ts IS NOT NULL
                WITH m, ($current_time - ts) / 86400000.0 as age_days
                RETURN
                    count(CASE WHEN age_days < 1 THEN 1 END) as last_day,
                    count(CASE WHEN age_days >= 1 AND age_days < 7 THEN 1 END) as last_week,
                    count(CASE WHEN age_days >= 7 AND age_days < 30 THEN 1 END) as last_month,
                    count(CASE WHEN age_days >= 30 AND age_days < 90 THEN 1 END) as last_3_months,
                    count(CASE WHEN age_days >= 90 THEN 1 END) as older
                """

                params = {'current_time': current_time, 'user_id': user_id} if user_id else {'current_time': current_time}
                result = await session.run(query, params)
                record = await result.single()

                if record:
                    return {
                        'last_day': record['last_day'] or 0,
                        'last_week': record['last_week'] or 0,
                        'last_month': record['last_month'] or 0,
                        'last_3_months': record['last_3_months'] or 0,
                        'older': record['older'] or 0
                    }
                else:
                    return {}

        except Exception as e:
            logging.error(f"Error getting age distribution: {e}", exc_info=True)
            return {'error': str(e)}

    async def get_access_patterns(self, user_id: str | None = None, limit: int = 20) -> dict:
        """
        Get memory access patterns.

        Args:
            user_id: Optional user filter
            limit: Number of top memories to return

        Returns:
            Access pattern statistics
        """
        try:
            driver = self.memory_db.get_driver()
            async with driver.session() as session:
                user_filter = ""
                if user_id:
                    user_filter = "MATCH (u:Person {id: $user_id})-[:IS_AUTHOR_OF]->(m)"

                query = f"""
                {user_filter}
                MATCH (m)
                WHERE m:EpisodicMemory OR m:KnowledgeUnit OR m:ProceduralUnit
                WITH m
                ORDER BY COALESCE(m.access_count, 0) DESC
                LIMIT $limit
                RETURN
                    m.id as id,
                    labels(m)[0] as type,
                    COALESCE(m.summary, m.statement, m.description) as content,
                    COALESCE(m.access_count, 0) as access_count,
                    COALESCE(m.importance, 0.5) as importance
                """

                params = {'limit': limit, 'user_id': user_id} if user_id else {'limit': limit}
                result = await session.run(query, params)

                top_accessed = []
                async for record in result:
                    top_accessed.append({
                        'id': record['id'],
                        'type': record['type'],
                        'content': record['content'][:100] + '...' if len(record['content']) > 100 else record['content'],
                        'access_count': record['access_count'],
                        'importance': record['importance']
                    })

                return {
                    'top_accessed_memories': top_accessed
                }

        except Exception as e:
            logging.error(f"Error getting access patterns: {e}", exc_info=True)
            return {'error': str(e)}

    async def get_concept_distribution(self, user_id: str | None = None, top_k: int = 20) -> list[dict]:
        """
        Get distribution of concepts across memories.

        Args:
            user_id: Optional user filter
            top_k: Number of top concepts to return

        Returns:
            List of concepts with counts
        """
        try:
            driver = self.memory_db.get_driver()
            async with driver.session() as session:
                user_filter = ""
                if user_id:
                    user_filter = "MATCH (u:Person {id: $user_id})-[:IS_AUTHOR_OF]->(m)"

                query = f"""
                {user_filter}
                MATCH (m)-[:REFERS_TO_CONCEPT]->(c:CONCEPT)
                WHERE m:EpisodicMemory OR m:KnowledgeUnit OR m:ProceduralUnit
                RETURN c.name as concept, count(m) as memory_count
                ORDER BY memory_count DESC
                LIMIT $top_k
                """

                params = {'top_k': top_k, 'user_id': user_id} if user_id else {'top_k': top_k}
                result = await session.run(query, params)

                concepts = []
                async for record in result:
                    concepts.append({
                        'concept': record['concept'],
                        'memory_count': record['memory_count']
                    })

                return concepts

        except Exception as e:
            logging.error(f"Error getting concept distribution: {e}", exc_info=True)
            return []

    async def diagnose_issues(self, user_id: str | None = None) -> dict:
        """
        Diagnose potential issues with memory system.

        Args:
            user_id: Optional user filter

        Returns:
            Dictionary with identified issues and recommendations
        """
        issues = []
        recommendations = []

        health = await self.get_system_health(user_id)
        db_health = health.get('database', {})
        quality = health.get('quality', {})

        total = db_health.get('total_memories', 0)
        cleanup_threshold = self._get_cleanup_threshold()
        if total > cleanup_threshold:
            issues.append(f"High memory count (>{cleanup_threshold:,})")
            recommendations.append("Run memory pruning to remove old, low-value memories")

        if total == 0:
            issues.append("No memories stored")
            recommendations.append("Verify memory extraction and storage is working")

        unused = quality.get('unused_memories', 0)
        if unused > total * 0.5 and total > 0:
            issues.append(f"High unused memory ratio ({unused}/{total})")
            recommendations.append("Consider pruning unused memories or improving retrieval")

        low_importance = quality.get('low_importance_memories', 0)
        if low_importance > total * 0.3 and total > 0:
            issues.append(f"High low-importance memory ratio ({low_importance}/{total})")
            recommendations.append("Run consolidation to merge or remove low-value memories")

        avg_access = quality.get('avg_access_count', 0)
        if avg_access < 1.0 and total > 100:
            issues.append(f"Low average access count ({avg_access:.2f})")
            recommendations.append("Verify retrieval system is working correctly")

        age_dist = await self.get_memory_age_distribution(user_id)
        older = age_dist.get('older', 0)
        if older > total * 0.7 and total > 0:
            issues.append(f"Most memories are old (>90 days): {older}/{total}")
            recommendations.append("Run memory consolidation and pruning")

        return {
            'issues': issues,
            'recommendations': recommendations,
            'severity': 'high' if len(issues) >= 3 else 'medium' if len(issues) >= 1 else 'low',
            'health_score': max(0, 100 - len(issues) * 20)
        }

    async def generate_report(self, user_id: str | None = None) -> str:
        """
        Generate comprehensive memory system report.

        Args:
            user_id: Optional user filter

        Returns:
            Formatted report string
        """
        health = await self.get_system_health(user_id)
        age_dist = await self.get_memory_age_distribution(user_id)
        access_patterns = await self.get_access_patterns(user_id, limit=10)
        concepts = await self.get_concept_distribution(user_id, top_k=10)
        diagnosis = await self.diagnose_issues(user_id)

        report = f"""
Memory System Report
{'=' * 60}
Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}
User: {user_id or 'All Users'}

DATABASE HEALTH
{'-' * 60}
Total Memories: {health['database']['total_memories']}
  - Episodic: {health['database']['episodic_memories']}
  - Knowledge: {health['database']['knowledge_units']}
  - Procedural: {health['database']['procedural_units']}

QUALITY METRICS
{'-' * 60}
Average Importance: {health['quality']['avg_importance']:.3f}
Average Access Count: {health['quality']['avg_access_count']:.2f}
Average Confidence: {health['quality']['avg_confidence']:.3f}
Unused Memories: {health['quality']['unused_memories']}
Low Importance: {health['quality']['low_importance_memories']}

AGE DISTRIBUTION
{'-' * 60}
Last 24 hours: {age_dist.get('last_day', 0)}
Last 7 days: {age_dist.get('last_week', 0)}
Last 30 days: {age_dist.get('last_month', 0)}
Last 90 days: {age_dist.get('last_3_months', 0)}
Older than 90 days: {age_dist.get('older', 0)}

TOP CONCEPTS
{'-' * 60}
"""
        for i, concept in enumerate(concepts[:10], 1):
            report += f"{i}. {concept['concept']}: {concept['memory_count']} memories\n"

        report += f"""
DIAGNOSIS
{'-' * 60}
Health Score: {diagnosis['health_score']}/100
Severity: {diagnosis['severity'].upper()}

Issues Found: {len(diagnosis['issues'])}
"""
        for issue in diagnosis['issues']:
            report += f"  - {issue}\n"

        if diagnosis['recommendations']:
            report += "\nRecommendations:\n"
            for rec in diagnosis['recommendations']:
                report += f"  - {rec}\n"

        report += f"\n{'=' * 60}\n"

        return report

    async def export_stats(self, user_id: str | None = None, format: str = 'json') -> str:
        """
        Export all statistics in specified format.

        Args:
            user_id: Optional user filter
            format: Export format ('json' or 'text')

        Returns:
            Formatted statistics
        """
        stats = {
            'health': await self.get_system_health(user_id),
            'age_distribution': await self.get_memory_age_distribution(user_id),
            'access_patterns': await self.get_access_patterns(user_id),
            'concepts': await self.get_concept_distribution(user_id),
            'diagnosis': await self.diagnose_issues(user_id)
        }

        if format == 'json':
            return json.dumps(stats, indent=2, default=str)
        else:
            return await self.generate_report(user_id)
