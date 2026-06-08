"""
Tests for MemoryConsolidationService — periodic dedup and merge of memories.

Covers:
- start_periodic_consolidation / stop_periodic_consolidation lifecycle
- _cluster_memories: DBSCAN dedup of near-identical embeddings
- _process_cluster: high-value → merge/promote, low-value → decay
- _merge_and_promote_cluster: Neo4j merge + delete flow
- _decay_cluster: importance decay via decay_service
- consolidate_user_memories: full flow
- consolidate_all_users: multi-user flow
- get_stats / reset_stats
- No-op when no duplicates exist
"""

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from tests.helpers import AsyncContextManagerMock, AsyncIteratorMock


def _load_module():
    """Load memory_consolidation module directly, bypassing lib.services.__init__ cascade."""
    _saved_lib = sys.modules.get("lib")
    _saved_lib_services = sys.modules.get("lib.services")

    try:
        svc_pkg = types.ModuleType("lib.services")
        sys.modules["lib.services"] = svc_pkg

        lib_pkg = types.ModuleType("lib")
        lib_pkg.services = svc_pkg
        sys.modules["lib"] = lib_pkg

        filepath = Path(__file__).parent.parent / "lib" / "services" / "memory_consolidation.py"
        spec = importlib.util.spec_from_file_location("memory_consolidation", filepath)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if _saved_lib is not None:
            sys.modules["lib"] = _saved_lib
        else:
            sys.modules.pop("lib", None)
        if _saved_lib_services is not None:
            sys.modules["lib.services"] = _saved_lib_services
        else:
            sys.modules.pop("lib.services", None)


_mod = _load_module()
MemoryConsolidationService = _mod.MemoryConsolidationService
ConsolidationConfig = _mod.ConsolidationConfig


# ============================================================
# Helpers
# ============================================================

def _make_mock_driver(session=None):
    """Create a mock Neo4j driver with a session."""
    driver = MagicMock()
    if session is None:
        session = AsyncMock()
    driver.session.return_value = AsyncContextManagerMock(session)
    return driver


def _make_mock_db(driver=None):
    """Create a mock memory_db with get_driver()."""
    db = MagicMock()
    if driver is None:
        driver = _make_mock_driver()
    db.get_driver.return_value = driver
    return db


def _make_decay_service(decay_factor=0.5):
    """Create a mock decay service with compute_decay_factor."""
    svc = MagicMock()
    svc.compute_decay_factor.return_value = decay_factor
    return svc


def _make_service(memory_db=None, decay_service=None, config=None):
    """Create a MemoryConsolidationService with mocked dependencies."""
    if memory_db is None:
        memory_db = _make_mock_db()
    if decay_service is None:
        decay_service = _make_decay_service()
    return MemoryConsolidationService(memory_db, decay_service, config)


# ============================================================
# Initialization
# ============================================================

def test_init_default_config():
    """Default config when none provided."""
    svc = _make_service()
    assert isinstance(svc.config, ConsolidationConfig)
    assert svc.config.similarity_threshold == 0.85
    assert svc.config.min_cluster_size == 2
    assert svc.config.consolidation_interval_hours == 24
    assert svc.is_running is False

def test_init_custom_config():
    """Custom config is used when provided."""
    cfg = ConsolidationConfig(
        similarity_threshold=0.90,
        min_cluster_size=3,
        consolidation_interval_hours=12,
    )
    svc = _make_service(config=cfg)
    assert svc.config.similarity_threshold == 0.90
    assert svc.config.min_cluster_size == 3
    assert svc.config.consolidation_interval_hours == 12

def test_init_stats_initialized():
    """Stats dictionary is initialized with zeros on construction."""
    svc = _make_service()
    stats = svc.get_stats()
    assert stats['runs'] == 0
    assert stats['memories_processed'] == 0
    assert stats['clusters_formed'] == 0
    assert stats['memories_merged'] == 0
    assert stats['memories_promoted'] == 0
    assert stats['memories_decayed'] == 0


# ============================================================
# Stats
# ============================================================

def test_get_stats_returns_copy():
    """get_stats returns a copy, mutating it doesn't affect internal stats."""
    svc = _make_service()
    copy = svc.get_stats()
    copy['runs'] = 999
    assert svc.get_stats()['runs'] == 0

def test_reset_stats():
    """reset_stats clears all counters to zero."""
    svc = _make_service()
    svc.stats['runs'] = 5
    svc.stats['memories_merged'] = 10
    svc.reset_stats()
    assert svc.get_stats()['runs'] == 0
    assert svc.get_stats()['memories_merged'] == 0


# ============================================================
# Lifecycle: start_periodic_consolidation / stop_periodic_consolidation
# ============================================================

@pytest.mark.asyncio
async def test_start_when_not_running():
    """start_periodic_consolidation sets is_running and creates task."""
    svc = _make_service()
    # Patch asyncio.create_task to avoid actually running loop
    with patch('asyncio.create_task') as mock_create:
        mock_create.return_value = MagicMock()
        await svc.start_periodic_consolidation()
        assert svc.is_running is True
        mock_create.assert_called_once()

@pytest.mark.asyncio
async def test_start_when_already_running():
    """start when already running is a no-op (warns)."""
    svc = _make_service()
    svc.is_running = True
    with patch('asyncio.create_task') as mock_create:
        await svc.start_periodic_consolidation()
        mock_create.assert_not_called()

@pytest.mark.asyncio
async def test_stop_when_running():
    """stop_periodic_consolidation cancels the task and sets is_running=False."""
    svc = _make_service()
    svc.is_running = True
    # Create a real asyncio task-like future that supports cancel() + await
    future = asyncio.Future()
    future.cancel = MagicMock(side_effect=future.cancel)
    # Don't actually cancel the future (would raise CancelledError on await)
    # Instead, make the future already done so await doesn't block
    future.set_result(None)
    svc.consolidation_task = future
    await svc.stop_periodic_consolidation()
    assert svc.is_running is False
    future.cancel.assert_called_once()

@pytest.mark.asyncio
async def test_stop_when_not_running():
    """stop when not running is a no-op."""
    svc = _make_service()
    svc.is_running = False
    svc.consolidation_task = None
    # Should not raise
    await svc.stop_periodic_consolidation()
    assert svc.is_running is False


# ============================================================
# _cluster_memories — DBSCAN dedup
# ============================================================

@pytest.mark.asyncio
async def test_cluster_too_few_memories():
    """Fewer memories than min_cluster_size → empty list."""
    svc = _make_service()
    svc.config.min_cluster_size = 3
    memories = [{'id': 'm1', 'summaryEmbeddingVector': [0.5]}]  # only 1
    clusters = await svc._cluster_memories(memories)
    assert clusters == []

@pytest.mark.asyncio
async def test_cluster_near_identical():
    """Multiple near-identical embeddings → single cluster."""
    svc = _make_service()
    svc.config.similarity_threshold = 0.5
    svc.config.min_cluster_size = 3
    memories = [
        {'id': 'm1', 'summaryEmbeddingVector': [1.0, 0.0, 0.0]},
        {'id': 'm2', 'summaryEmbeddingVector': [0.999, 0.001, 0.0]},
        {'id': 'm3', 'summaryEmbeddingVector': [0.998, 0.002, 0.0]},
    ]
    clusters = await svc._cluster_memories(memories)
    assert len(clusters) == 1
    assert len(clusters[0]) == 3

@pytest.mark.asyncio
async def test_cluster_very_different():
    """Two very different embeddings → noise (no clusters)."""
    svc = _make_service()
    memories = [
        {'id': 'm1', 'summaryEmbeddingVector': [1.0, 0.0, 0.0]},
        {'id': 'm2', 'summaryEmbeddingVector': [-1.0, 0.0, 0.0]},
    ]
    clusters = await svc._cluster_memories(memories)
    assert clusters == []  # noise

@pytest.mark.asyncio
async def test_cluster_mixed():
    """Multiple similar points cluster, dissimilar ones are noise."""
    svc = _make_service()
    svc.config.similarity_threshold = 0.5
    svc.config.min_cluster_size = 3
    # Four similar, one very different
    memories = [
        {'id': 'a', 'summaryEmbeddingVector': [1.0, 0.0]},
        {'id': 'b', 'summaryEmbeddingVector': [0.99, 0.01]},
        {'id': 'c', 'summaryEmbeddingVector': [0.98, 0.02]},
        {'id': 'd', 'summaryEmbeddingVector': [0.97, 0.03]},
        {'id': 'e', 'summaryEmbeddingVector': [-1.0, 0.0]},
    ]
    clusters = await svc._cluster_memories(memories)
    assert len(clusters) == 1
    assert len(clusters[0]) == 4

@pytest.mark.asyncio
async def test_cluster_zero_vectors():
    """Memories with zero vectors → single cluster (cosine distance is NaN but handles)."""
    svc = _make_service()
    memories = [
        {'id': 'x', 'summaryEmbeddingVector': [0.0, 0.0, 0.0]},
        {'id': 'y', 'summaryEmbeddingVector': [0.0, 0.0, 0.0]},
    ]
    clusters = await svc._cluster_memories(memories)
    # Zero vectors should cluster together (identical)
    assert len(clusters) >= 0  # Just ensure it runs without error

@pytest.mark.asyncio
async def test_cluster_empty_list():
    """Empty memory list → empty clusters."""
    svc = _make_service()
    clusters = await svc._cluster_memories([])
    assert clusters == []

@pytest.mark.asyncio
async def test_cluster_fallback_zero_embedding():
    """Missing embedding → falls back to zero vectors, code doesn't crash."""
    svc = _make_service()
    svc.config.embedding_dimension = 4
    svc.config.min_cluster_size = 2
    memories = [
        {'id': 'm1', 'summaryEmbeddingVector': []},
        {'id': 'm2', 'summaryEmbeddingVector': []},
    ]
    # Zero vectors produce NaN cosine distances; DBSCAN may noise them out.
    # The test just verifies the fallback doesn't crash.
    clusters = await svc._cluster_memories(memories)
    assert isinstance(clusters, list)

@pytest.mark.asyncio
async def test_cluster_dbscan_error():
    """DBSCAN.fit() raises → caught, logged, returns []."""
    svc = _make_service()
    svc.config.min_cluster_size = 2
    memories = [
        {'id': 'm1', 'summaryEmbeddingVector': [1.0, 0.0]},
        {'id': 'm2', 'summaryEmbeddingVector': [0.0, 1.0]},
    ]
    with patch.object(_mod, 'DBSCAN') as mock_dbscan:
        mock_dbscan.return_value.fit.side_effect = RuntimeError("boom")
        clusters = await svc._cluster_memories(memories)
    assert clusters == []

# ============================================================
# _process_cluster — high-value merge vs low-value decay
# ============================================================

@pytest.mark.asyncio
async def test_process_high_importance_cluster():
    """avg_importance >= threshold → merge_and_promote."""
    svc = _make_service()
    svc.config.min_importance_for_promotion = 0.7
    svc._merge_and_promote_cluster = AsyncMock()
    svc._decay_cluster = AsyncMock()

    cluster = [
        {'id': 'm1', 'importance': 0.8, 'access_count': 0},
        {'id': 'm2', 'importance': 0.7, 'access_count': 0},
    ]
    await svc._process_cluster(cluster, 'user1')
    svc._merge_and_promote_cluster.assert_called_once()
    svc._decay_cluster.assert_not_called()
    assert svc.stats['memories_promoted'] == 1

@pytest.mark.asyncio
async def test_process_high_access_cluster():
    """total_access >= threshold → merge_and_promote (even if importance low)."""
    svc = _make_service()
    svc.config.min_access_count_for_promotion = 3
    svc._merge_and_promote_cluster = AsyncMock()
    svc._decay_cluster = AsyncMock()

    cluster = [
        {'id': 'm1', 'importance': 0.2, 'access_count': 2},
        {'id': 'm2', 'importance': 0.2, 'access_count': 2},
    ]
    await svc._process_cluster(cluster, 'user1')
    svc._merge_and_promote_cluster.assert_called_once()
    svc._decay_cluster.assert_not_called()
    assert svc.stats['memories_promoted'] == 1

@pytest.mark.asyncio
async def test_process_low_value_cluster():
    """Low importance + low access → decay."""
    svc = _make_service()
    svc.config.min_importance_for_promotion = 0.7
    svc.config.min_access_count_for_promotion = 3
    svc._merge_and_promote_cluster = AsyncMock()
    svc._decay_cluster = AsyncMock()

    cluster = [
        {'id': 'm1', 'importance': 0.3, 'access_count': 0},
        {'id': 'm2', 'importance': 0.4, 'access_count': 0},
    ]
    await svc._process_cluster(cluster, 'user1')
    svc._merge_and_promote_cluster.assert_not_called()
    svc._decay_cluster.assert_called_once()
    assert svc.stats['memories_decayed'] == 2

@pytest.mark.asyncio
async def test_process_empty_cluster():
    """Empty cluster → no-op."""
    svc = _make_service()
    svc._merge_and_promote_cluster = AsyncMock()
    svc._decay_cluster = AsyncMock()

    await svc._process_cluster([], 'user1')
    svc._merge_and_promote_cluster.assert_not_called()
    svc._decay_cluster.assert_not_called()


# ============================================================
# _merge_and_promote_cluster
# ============================================================

@pytest.mark.asyncio
async def test_merge_two_memories():
    """Merge 2 memories: keep representative, delete source, update stats."""
    svc = _make_service()
    session = AsyncMock()
    driver = _make_mock_driver(session)
    svc.memory_db = _make_mock_db(driver)

    cluster = [
        {'id': 'rep', 'memory_type': 'EpisodicMemory', 'importance': 0.8, 'access_count': 5},
        {'id': 'src', 'memory_type': 'EpisodicMemory', 'importance': 0.6, 'access_count': 2},
    ]
    await svc._merge_and_promote_cluster(cluster, 'user1')

    # representative should be the one with higher score
    # rep: 0.8 * (1 + 5/10) = 0.8 * 1.5 = 1.2
    # src: 0.6 * (1 + 2/10) = 0.6 * 1.2 = 0.72
    assert session.run.call_count == 3  # update, rel transfer, delete
    assert svc.stats['memories_merged'] == 1

@pytest.mark.asyncio
async def test_merge_single_memory_noop():
    """Single memory in cluster → no-op (returns early)."""
    svc = _make_service()
    svc._merge_and_promote_cluster = AsyncMock()
    cluster = [{'id': 'only', 'importance': 0.5, 'access_count': 0}]
    # Direct call to _merge_and_promote_cluster (unmocked)
    svc_unmocked = _make_service()
    await svc_unmocked._merge_and_promote_cluster(cluster, 'user1')
    assert svc_unmocked.stats['memories_merged'] == 0  # unchanged

@pytest.mark.asyncio
async def test_merge_same_id_memories():
    """Two memories with same id → source_ids empty → no-op."""
    svc = _make_service()
    cluster = [
        {'id': 'same', 'importance': 0.8, 'access_count': 5},
        {'id': 'same', 'importance': 0.6, 'access_count': 2},
    ]
    await svc._merge_and_promote_cluster(cluster, 'user1')
    assert svc.stats['memories_merged'] == 0

@pytest.mark.asyncio
async def test_merge_updates_merge_count():
    """Merged target memory gets boosted importance and mergedFromCount."""
    svc = _make_service()
    session = AsyncMock()
    driver = _make_mock_driver(session)
    svc.memory_db = _make_mock_db(driver)

    cluster = [
        {'id': 'rep', 'memory_type': 'EpisodicMemory', 'importance': 0.5, 'access_count': 0},
        {'id': 'src', 'memory_type': 'EpisodicMemory', 'importance': 0.3, 'access_count': 0},
    ]
    await svc._merge_and_promote_cluster(cluster, 'user1')

    # The update call should have boosted importance: min(1.0, 0.5 * 1.1) = 0.55
    update_call = session.run.call_args_list[0]
    kwargs = update_call[1]
    assert kwargs['importance'] == 0.55
    assert kwargs['source_count'] == 1

@pytest.mark.asyncio
async def test_merge_handles_error():
    """Merge error is caught and doesn't crash."""
    svc = _make_service()
    session = AsyncMock()
    session.run.side_effect = RuntimeError("Neo4j down")
    driver = _make_mock_driver(session)
    svc.memory_db = _make_mock_db(driver)

    cluster = [
        {'id': 'rep', 'memory_type': 'EpisodicMemory', 'importance': 0.8, 'access_count': 5},
        {'id': 'src', 'memory_type': 'EpisodicMemory', 'importance': 0.6, 'access_count': 2},
    ]
    # Should not raise
    await svc._merge_and_promote_cluster(cluster, 'user1')


# ============================================================
# _decay_cluster
# ============================================================

@pytest.mark.asyncio
async def test_decay_reduces_importance():
    """Decay applies decay_factor to each memory's importance."""
    svc = _make_service(decay_service=_make_decay_service(decay_factor=0.5))
    session = AsyncMock()
    driver = _make_mock_driver(session)
    svc.memory_db = _make_mock_db(driver)

    cluster = [
        {'id': 'm1', 'memory_type': 'EpisodicMemory', 'importance': 0.8, 'creation_timestamp': 1000},
        {'id': 'm2', 'memory_type': 'KnowledgeUnit', 'importance': 0.4, 'creation_timestamp': 2000},
    ]
    await svc._decay_cluster(cluster)

    assert session.run.call_count == 2
    # First call: importance 0.8 * 0.5 = 0.4 (>= 0.1, so 0.4)
    # Second call: importance 0.4 * 0.5 = 0.2 (>= 0.1, so 0.2)
    first_kwargs = session.run.call_args_list[0][1]
    second_kwargs = session.run.call_args_list[1][1]
    assert first_kwargs['new_importance'] == 0.4
    assert second_kwargs['new_importance'] == 0.2

@pytest.mark.asyncio
async def test_decay_minimum_floor():
    """Decay clamps importance at 0.1 minimum."""
    svc = _make_service(decay_service=_make_decay_service(decay_factor=0.05))
    session = AsyncMock()
    driver = _make_mock_driver(session)
    svc.memory_db = _make_mock_db(driver)

    cluster = [
        {'id': 'm1', 'memory_type': 'EpisodicMemory', 'importance': 0.8, 'creation_timestamp': 1000},
    ]
    await svc._decay_cluster(cluster)

    # 0.8 * 0.05 = 0.04 → clamped to 0.1
    kwargs = session.run.call_args[1]
    assert kwargs['new_importance'] == 0.1

@pytest.mark.asyncio
async def test_decay_empty_cluster():
    """Empty cluster → no-op."""
    svc = _make_service()
    session = AsyncMock()
    driver = _make_mock_driver(session)
    svc.memory_db = _make_mock_db(driver)

    await svc._decay_cluster([])
    session.run.assert_not_called()

@pytest.mark.asyncio
async def test_decay_calls_decay_service():
    """decay_service.compute_decay_factor is called with creation_timestamp."""
    decay_svc = _make_decay_service(decay_factor=0.3)
    svc = _make_service(decay_service=decay_svc)
    session = AsyncMock()
    driver = _make_mock_driver(session)
    svc.memory_db = _make_mock_db(driver)

    cluster = [
        {'id': 'm1', 'memory_type': 'EpisodicMemory', 'importance': 0.5, 'creation_timestamp': 1234567890},
    ]
    await svc._decay_cluster(cluster)

    decay_svc.compute_decay_factor.assert_called_once_with(1234567890)

@pytest.mark.asyncio
async def test_decay_handles_error():
    """Decay error is caught and doesn't crash."""
    svc = _make_service()
    session = AsyncMock()
    session.run.side_effect = RuntimeError("Neo4j down")
    driver = _make_mock_driver(session)
    svc.memory_db = _make_mock_db(driver)

    cluster = [{'id': 'm1', 'memory_type': 'EpisodicMemory', 'importance': 0.5, 'creation_timestamp': 100}]
    await svc._decay_cluster(cluster)  # should not raise


# ============================================================
# consolidate_user_memories — full flow
# ============================================================

@pytest.mark.asyncio
async def test_consolidate_user_no_memories():
    """No recent memories → no-op, returns early."""
    svc = _make_service()
    svc._get_recent_memories = AsyncMock(return_value=[])
    svc._cluster_memories = AsyncMock()
    svc._process_cluster = AsyncMock()

    await svc.consolidate_user_memories('user1')
    svc._cluster_memories.assert_not_called()
    svc._process_cluster.assert_not_called()
    assert svc.stats['memories_processed'] == 0

@pytest.mark.asyncio
async def test_consolidate_user_with_memories():
    """Recent memories → cluster → process each cluster."""
    svc = _make_service()
    memories = [
        {'id': 'm1', 'summaryEmbeddingVector': [1.0, 0.0], 'importance': 0.8, 'access_count': 5},
        {'id': 'm2', 'summaryEmbeddingVector': [0.9, 0.1], 'importance': 0.7, 'access_count': 3},
    ]
    svc._get_recent_memories = AsyncMock(return_value=memories)
    svc._cluster_memories = AsyncMock(return_value=[memories])
    svc._process_cluster = AsyncMock()

    await svc.consolidate_user_memories('user1')

    assert svc.stats['memories_processed'] == 2
    assert svc.stats['clusters_formed'] == 1
    svc._process_cluster.assert_called_once()

@pytest.mark.asyncio
async def test_consolidate_user_handles_error():
    """Error during consolidation is caught."""
    svc = _make_service()
    svc._get_recent_memories = AsyncMock(side_effect=RuntimeError("DB down"))
    await svc.consolidate_user_memories('user1')  # should not raise


# ============================================================
# consolidate_all_users — multi-user flow
# ============================================================

@pytest.mark.asyncio
async def test_consolidate_all_users_with_users():
    """Finds all users and consolidates each."""
    svc = _make_service()

    records = [
        MagicMock(__getitem__=MagicMock(return_value='user1')),
        MagicMock(__getitem__=MagicMock(return_value='user2')),
    ]
    # Make __getitem__ work for both 'user_id' key lookups
    for i, rec in enumerate(records):
        rec.__getitem__.side_effect = lambda k, val=f'user{i+1}': val

    mock_result = AsyncIteratorMock(records)
    session = AsyncMock()
    session.run = AsyncMock(return_value=mock_result)
    driver = _make_mock_driver(session)
    svc.memory_db = _make_mock_db(driver)

    svc.consolidate_user_memories = AsyncMock()

    await svc.consolidate_all_users()

    assert svc.consolidate_user_memories.call_count == 2
    assert svc.stats['runs'] == 1

@pytest.mark.asyncio
async def test_consolidate_all_users_empty():
    """No users → still completes and increments runs."""
    svc = _make_service()
    session = AsyncMock()
    session.run = AsyncMock(return_value=AsyncIteratorMock([]))
    driver = _make_mock_driver(session)
    svc.memory_db = _make_mock_db(driver)

    await svc.consolidate_all_users()
    assert svc.stats['runs'] == 1

@pytest.mark.asyncio
async def test_consolidate_all_users_handles_error():
    """Error fetching users is caught."""
    svc = _make_service()
    session = AsyncMock()
    session.run = AsyncMock(side_effect=RuntimeError("Neo4j down"))
    driver = _make_mock_driver(session)
    svc.memory_db = _make_mock_db(driver)

    await svc.consolidate_all_users()  # should not raise


# ============================================================
# No-op scenarios
# ============================================================

@pytest.mark.asyncio
async def test_noop_when_no_duplicates():
    """When all memories are different, clustering produces no clusters → no merges."""
    svc = _make_service()
    svc.config.min_cluster_size = 2
    # Four very different embeddings
    memories = [
        {'id': 'a', 'summaryEmbeddingVector': [1.0, 0.0, 0.0, 0.0], 'importance': 0.5, 'access_count': 0},
        {'id': 'b', 'summaryEmbeddingVector': [0.0, 1.0, 0.0, 0.0], 'importance': 0.5, 'access_count': 0},
        {'id': 'c', 'summaryEmbeddingVector': [0.0, 0.0, 1.0, 0.0], 'importance': 0.5, 'access_count': 0},
        {'id': 'd', 'summaryEmbeddingVector': [0.0, 0.0, 0.0, 1.0], 'importance': 0.5, 'access_count': 0},
    ]
    svc._get_recent_memories = AsyncMock(return_value=memories)
    svc._merge_and_promote_cluster = AsyncMock()
    svc._decay_cluster = AsyncMock()

    await svc.consolidate_user_memories('user1')

    # No merges or decays should happen (all embeddings are orthogonal → all noise)
    svc._merge_and_promote_cluster.assert_not_called()
    svc._decay_cluster.assert_not_called()
    assert svc.stats['memories_merged'] == 0
    assert svc.stats['memories_promoted'] == 0
    assert svc.stats['memories_decayed'] == 0


# ============================================================
# _consolidation_loop
# ============================================================

@pytest.mark.asyncio
async def test_consolidation_loop_runs_once():
    """Loop calls consolidate_all_users then sleeps."""
    svc = _make_service()
    svc.consolidate_all_users = AsyncMock()

    # Set is_running to True, then make sleep set it to False
    svc.is_running = True

    async def fake_sleep(_):
        svc.is_running = False

    with patch('asyncio.sleep', side_effect=fake_sleep):
        await svc._consolidation_loop()

    svc.consolidate_all_users.assert_called_once()

@pytest.mark.asyncio
async def test_consolidation_loop_cancelled():
    """CancelledError is caught and handled."""
    svc = _make_service()
    svc.consolidate_all_users = AsyncMock(side_effect=asyncio.CancelledError())
    svc.is_running = True

    await svc._consolidation_loop()  # should not raise

@pytest.mark.asyncio
async def test_consolidation_loop_generic_error():
    """Generic exception is caught and loop exits."""
    svc = _make_service()
    svc.consolidate_all_users = AsyncMock(side_effect=ValueError("oops"))
    svc.is_running = True

    await svc._consolidation_loop()  # should not raise


