"""
Memory decay service - implements forgetting mechanisms.
Based on Ebbinghaus forgetting curve and confidence-weighted decay.
"""
import logging
import math
import time
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class DecayConfig:
    """Configuration for memory decay."""

    # Ebbinghaus curve parameters
    half_life_days: float = 30.0  # Time for memory strength to decay by 50%

    # Weight factors for relevance scoring
    similarity_weight: float = 0.4
    decay_weight: float = 0.3
    access_weight: float = 0.2
    confidence_weight: float = 0.1

    # Threshold for pruning
    min_access_count: int = 2
    min_importance: float = 0.3
    max_age_days: int = 90

    # Boosting factors
    max_access_boost: float = 1.0
    access_boost_divisor: int = 10


class MemoryDecayService:
    """
    Service for computing memory decay and relevance scores.

    Implements:
    - Ebbinghaus forgetting curve
    - Access frequency boosting
    - Confidence weighting
    - Age-based decay
    """

    def __init__(self, config: Optional[DecayConfig] = None):
        """
        Initialize memory decay service.

        Args:
            config: Decay configuration (uses defaults if not provided)
        """
        self.config = config or DecayConfig()
        logging.info(
            f"Memory decay service initialized with half-life={self.config.half_life_days} days"
        )

    def compute_decay_factor(self, creation_timestamp: int, current_time: Optional[float] = None) -> float:
        """
        Compute Ebbinghaus forgetting curve decay factor.

        Formula: decay = exp(-age_hours / (half_life_days * 24))

        Args:
            creation_timestamp: Memory creation timestamp (milliseconds)
            current_time: Current time (seconds, defaults to now)

        Returns:
            Decay factor between 0.0 and 1.0
        """
        if current_time is None:
            current_time = time.time()

        # Convert creation timestamp from milliseconds to seconds
        creation_time_sec = creation_timestamp / 1000.0

        # Calculate age in hours
        age_seconds = current_time - creation_time_sec
        age_hours = max(0, age_seconds / 3600.0)

        # Apply Ebbinghaus curve
        half_life_hours = self.config.half_life_days * 24.0
        decay_factor = math.exp(-age_hours / half_life_hours)

        return min(1.0, max(0.0, decay_factor))

    def compute_access_boost(self, access_count: int) -> float:
        """
        Compute boost factor based on access frequency.

        More frequently accessed memories get higher boost.

        Args:
            access_count: Number of times memory was accessed

        Returns:
            Boost factor between 0.0 and 1.0
        """
        boost = min(
            access_count / self.config.access_boost_divisor,
            self.config.max_access_boost
        )
        return boost

    def compute_relevance_score(
        self,
        similarity_score: float,
        creation_timestamp: int,
        access_count: int = 0,
        confidence_score: Optional[float] = None,
        importance: Optional[float] = None,
        current_time: Optional[float] = None
    ) -> Dict[str, float]:
        """
        Compute overall relevance score combining multiple factors.

        Factors:
        - Similarity to query (40%)
        - Time decay (30%)
        - Access frequency (20%)
        - Confidence (10%)

        Args:
            similarity_score: Cosine similarity to query (0-1)
            creation_timestamp: Memory creation time (milliseconds)
            access_count: Number of accesses
            confidence_score: Confidence in memory accuracy (0-1)
            importance: Manual importance rating (0-1)
            current_time: Current time (seconds)

        Returns:
            Dictionary with relevance score and components
        """
        # Compute components
        decay_factor = self.compute_decay_factor(creation_timestamp, current_time)
        access_boost = self.compute_access_boost(access_count)
        confidence = confidence_score or 0.5

        # Apply importance multiplier if provided
        importance_mult = importance or 1.0

        # Weighted combination
        relevance = (
            similarity_score * self.config.similarity_weight +
            decay_factor * self.config.decay_weight +
            access_boost * self.config.access_weight +
            confidence * self.config.confidence_weight
        ) * importance_mult

        return {
            'relevance': min(1.0, relevance),
            'similarity': similarity_score,
            'decay_factor': decay_factor,
            'access_boost': access_boost,
            'confidence': confidence,
            'importance_mult': importance_mult
        }

    def should_prune(
        self,
        creation_timestamp: int,
        access_count: int,
        importance: float,
        current_time: Optional[float] = None
    ) -> bool:
        """
        Determine if a memory should be pruned.

        Prune if:
        - Low access count AND old age AND low importance

        Args:
            creation_timestamp: Memory creation time (milliseconds)
            access_count: Number of accesses
            importance: Importance rating (0-1)
            current_time: Current time (seconds)

        Returns:
            True if memory should be pruned
        """
        if current_time is None:
            current_time = time.time()

        # Calculate age in days
        creation_time_sec = creation_timestamp / 1000.0
        age_days = (current_time - creation_time_sec) / 86400.0

        # Check pruning criteria
        is_old = age_days > self.config.max_age_days
        is_rarely_accessed = access_count < self.config.min_access_count
        is_unimportant = importance < self.config.min_importance

        should_prune = is_old and is_rarely_accessed and is_unimportant

        if should_prune:
            logging.debug(
                f"Memory marked for pruning: age={age_days:.1f}d, "
                f"access_count={access_count}, importance={importance:.2f}"
            )

        return should_prune

    def rank_memories(
        self,
        memories: List[Dict],
        query_embedding: Optional[List[float]] = None,
        current_time: Optional[float] = None
    ) -> List[Dict]:
        """
        Rank memories by relevance score with decay applied.

        Args:
            memories: List of memory dictionaries
            query_embedding: Optional query embedding for similarity
            current_time: Current time (seconds)

        Returns:
            Ranked list of memories with computed scores
        """
        ranked = []

        for mem in memories:
            # Extract memory properties
            similarity = mem.get('score', 0.5)
            creation_ts = mem.get('creationTimestamp', int(time.time() * 1000))
            access_count = mem.get('access_count', 0)
            confidence = mem.get('confidenceScore', None)
            importance = mem.get('importance', 0.5)

            # Compute relevance
            relevance_data = self.compute_relevance_score(
                similarity_score=similarity,
                creation_timestamp=creation_ts,
                access_count=access_count,
                confidence_score=confidence,
                importance=importance,
                current_time=current_time
            )

            # Add relevance data to memory
            mem_copy = mem.copy()
            mem_copy['relevance_score'] = relevance_data['relevance']
            mem_copy['decay_components'] = relevance_data

            ranked.append(mem_copy)

        # Sort by relevance score
        ranked.sort(key=lambda x: x['relevance_score'], reverse=True)

        return ranked

    def get_prunable_memories(
        self,
        memories: List[Dict],
        current_time: Optional[float] = None
    ) -> List[Dict]:
        """
        Filter memories that should be pruned.

        Args:
            memories: List of memory dictionaries
            current_time: Current time (seconds)

        Returns:
            List of memories that should be pruned
        """
        prunable = []

        for mem in memories:
            creation_ts = mem.get('creationTimestamp', int(time.time() * 1000))
            access_count = mem.get('access_count', 0)
            importance = mem.get('importance', 0.5)

            if self.should_prune(creation_ts, access_count, importance, current_time):
                prunable.append(mem)

        logging.info(f"Found {len(prunable)} prunable memories out of {len(memories)}")
        return prunable

    def compute_consolidation_priority(
        self,
        memories: List[Dict],
        current_time: Optional[float] = None
    ) -> List[Dict]:
        """
        Compute consolidation priority for memories.

        High priority memories should be:
        - Recent
        - Frequently accessed
        - High importance
        - High confidence

        Args:
            memories: List of memory dictionaries
            current_time: Current time (seconds)

        Returns:
            Memories sorted by consolidation priority
        """
        prioritized = []

        for mem in memories:
            creation_ts = mem.get('creationTimestamp', int(time.time() * 1000))
            access_count = mem.get('access_count', 0)
            importance = mem.get('importance', 0.5)
            confidence = mem.get('confidenceScore', 0.5)

            # Calculate recency (inverse of age)
            if current_time is None:
                current_time = time.time()

            creation_time_sec = creation_ts / 1000.0
            age_days = (current_time - creation_time_sec) / 86400.0
            recency = 1.0 / (1.0 + age_days)  # Newer = higher

            # Calculate consolidation priority
            priority = (
                recency * 0.3 +
                min(access_count / 10.0, 1.0) * 0.3 +
                importance * 0.2 +
                confidence * 0.2
            )

            mem_copy = mem.copy()
            mem_copy['consolidation_priority'] = priority
            mem_copy['recency'] = recency
            prioritized.append(mem_copy)

        # Sort by priority
        prioritized.sort(key=lambda x: x['consolidation_priority'], reverse=True)

        return prioritized

    def update_config(self, **kwargs):
        """
        Update decay configuration.

        Args:
            **kwargs: Configuration parameters to update
        """
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
                logging.info(f"Updated decay config: {key}={value}")
            else:
                logging.warning(f"Unknown config parameter: {key}")
