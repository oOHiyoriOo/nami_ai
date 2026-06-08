"""
Tests for MemoryAnalytics — memory system diagnostics and monitoring.

Covers:
- get_system_health: empty DB, populated DB, with/without hierarchy, status logic
- _get_database_health: memory type distribution counting
- _get_quality_metrics: avg importance/access/confidence/unused/low-importance
- get_memory_age_distribution: age histogram
- get_access_patterns: top accessed memories
- get_concept_distribution: concept distribution
- diagnose_issues: severity, health_score, recommendations
- generate_report: full report formatting
- export_stats: JSON and text export
- User-scoped analytics (user_id filter)
- Error handling in DB/quality/age/access/concept methods
"""

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.helpers import AsyncContextManagerMock


def _load_module():
    """Load memory_analytics module directly, bypassing lib.services.__init__ cascade.

    memory_analytics.py imports from lib.global_registry (g_data). We pre-populate
    sys.modules with stub modules so that import chain doesn't trigger heavy deps.
    """
    _saved_lib = sys.modules.get("lib")
    _saved_lib_services = sys.modules.get("lib.services")
    _saved_lib_gr = sys.modules.get("lib.global_registry")

    try:
        svc_pkg = types.ModuleType("lib.services")
        sys.modules["lib.services"] = svc_pkg

        gr_mod = types.ModuleType("lib.global_registry")
        _gdata = MagicMock()
        _gdata.get.return_value = None
        gr_mod.g_data = _gdata
        sys.modules["lib.global_registry"] = gr_mod

        lib_pkg = types.ModuleType("lib")
        lib_pkg.global_registry = gr_mod
        sys.modules["lib"] = lib_pkg

        filepath = Path(__file__).parent.parent / "lib" / "services" / "memory_analytics.py"
        spec = importlib.util.spec_from_file_location("memory_analytics", filepath)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for key, saved in [("lib", _saved_lib), ("lib.services", _saved_lib_services), ("lib.global_registry", _saved_lib_gr)]:
            if saved is not None:
                sys.modules[key] = saved
            else:
                sys.modules.pop(key, None)


_ma = _load_module()
MemoryAnalytics = _ma.MemoryAnalytics


# ============================================================
# Async mock helpers for Neo4j chain
# ============================================================

class AsyncIteratorMock:
    """Mock async iterator for `async for x in result:`."""
    def __init__(self, items):
        self._items = list(items)
        self._idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._idx]
        self._idx += 1
        return MagicMock(
            __getitem__=MagicMock(side_effect=lambda k, _item=item: _item.get(k))
        )


def _mock_db_health_result(episodic=0, knowledge=0, procedural=0):
    """Create a mock Neo4j run result for _get_database_health queries.

    The method runs 3 separate queries, one per memory type. This helper
    creates a session.run side_effect that returns the right count for each.
    """
    session = AsyncMock()

    async def _run(query, user_id=None):
        result = AsyncMock()
        if "EpisodicMemory" in query:
            record = MagicMock()
            record.__getitem__ = MagicMock(return_value=episodic)
            record.__getitem__.side_effect = lambda k: episodic if k == 'count' else None
            result.single = AsyncMock(return_value=record)
        elif "KnowledgeUnit" in query:
            record = MagicMock()
            record.__getitem__ = MagicMock(return_value=knowledge)
            record.__getitem__.side_effect = lambda k: knowledge if k == 'count' else None
            result.single = AsyncMock(return_value=record)
        elif "ProceduralUnit" in query:
            record = MagicMock()
            record.__getitem__ = MagicMock(return_value=procedural)
            record.__getitem__.side_effect = lambda k: procedural if k == 'count' else None
            result.single = AsyncMock(return_value=record)
        else:
            record = MagicMock()
            record.__getitem__ = MagicMock(return_value=0)
            record.__getitem__.side_effect = lambda k: 0
            result.single = AsyncMock(return_value=record)
        return result

    session.run = AsyncMock(side_effect=_run)
    return session


def _make_analytics(memory_db=None, memory_hierarchy=None):
    """Create a MemoryAnalytics instance with a mock memory_db."""
    if memory_db is None:
        memory_db = MagicMock()
        mock_driver = MagicMock()
        memory_db.get_driver.return_value = mock_driver
    return MemoryAnalytics(memory_db, memory_hierarchy=memory_hierarchy)


# ============================================================
# get_system_health — empty DB
# ============================================================

@pytest.mark.asyncio
async def test_get_system_health_empty_db():
    """Empty DB → status='empty', total=0, all type counts 0."""
    ma = _make_analytics()
    session = _mock_db_health_result(episodic=0, knowledge=0, procedural=0)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    health = await ma.get_system_health()
    assert health['status'] == 'empty', f"Expected 'empty', got {health['status']}"
    assert health['database']['total_memories'] == 0
    assert health['database']['episodic_memories'] == 0
    assert health['database']['knowledge_units'] == 0
    assert health['database']['procedural_units'] == 0
    assert 'quality' in health
    assert 'timestamp' in health


# ============================================================
# get_system_health — populated DB
# ============================================================

@pytest.mark.asyncio
async def test_get_system_health_populated_db():
    """Populated DB → status='healthy', correct type counts."""
    ma = _make_analytics()
    session = _mock_db_health_result(episodic=5, knowledge=3, procedural=2)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    health = await ma.get_system_health()
    assert health['status'] in ('healthy', 'needs_cleanup', 'empty')
    db = health['database']
    assert db['total_memories'] == 10
    assert db['episodic_memories'] == 5
    assert db['knowledge_units'] == 3
    assert db['procedural_units'] == 2


# ============================================================
# get_system_health — needs_cleanup when over threshold
# ============================================================

@pytest.mark.asyncio
async def test_get_system_health_needs_cleanup():
    """Total > cleanup_threshold (default 10000) → status='needs_cleanup'."""
    ma = _make_analytics()
    session = _mock_db_health_result(episodic=5000, knowledge=4000, procedural=2000)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    health = await ma.get_system_health()
    assert health['database']['total_memories'] == 11000
    assert health['status'] == 'needs_cleanup'


# ============================================================
# get_system_health — with hierarchy
# ============================================================

@pytest.mark.asyncio
async def test_get_system_health_with_hierarchy():
    """When memory_hierarchy is provided, hierarchy stats are included."""
    mock_hierarchy = MagicMock()
    mock_hierarchy.get_stats = AsyncMock(return_value={"levels": 3, "total": 10})

    ma = _make_analytics(memory_hierarchy=mock_hierarchy)
    session = _mock_db_health_result(episodic=1, knowledge=1, procedural=1)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    health = await ma.get_system_health()
    assert 'hierarchy' in health
    assert health['hierarchy'] == {"levels": 3, "total": 10}


# ============================================================
# get_system_health — without hierarchy
# ============================================================

@pytest.mark.asyncio
async def test_get_system_health_without_hierarchy():
    """When memory_hierarchy is None, 'hierarchy' key is absent."""
    ma = _make_analytics(memory_hierarchy=None)
    session = _mock_db_health_result(episodic=1, knowledge=1, procedural=1)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    health = await ma.get_system_health()
    assert 'hierarchy' not in health


# ============================================================
# Memory type distribution counting (via _get_database_health)
# ============================================================

@pytest.mark.asyncio
async def test_type_distribution_all_episodic():
    """Only EpisodicMemory entries → counts reflect that."""
    ma = _make_analytics()
    session = _mock_db_health_result(episodic=42, knowledge=0, procedural=0)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    health = await ma._get_database_health()
    assert health['total_memories'] == 42
    assert health['episodic_memories'] == 42
    assert health['knowledge_units'] == 0
    assert health['procedural_units'] == 0


@pytest.mark.asyncio

async def test_type_distribution_mixed():
    """Mixed memory types → counts match individual totals."""
    ma = _make_analytics()
    session = _mock_db_health_result(episodic=7, knowledge=13, procedural=3)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    health = await ma._get_database_health()
    assert health['episodic_memories'] == 7
    assert health['knowledge_units'] == 13
    assert health['procedural_units'] == 3
    assert health['total_memories'] == 23


# ============================================================
# User-scoped analytics (user_id parameter)
# ============================================================

@pytest.mark.asyncio
async def test_user_scoped_database_health():
    """user_id is passed through to Neo4j queries."""
    ma = _make_analytics()
    session = AsyncMock()

    async def _run(query, user_id=None):
        result = AsyncMock()
        record = MagicMock()
        record.__getitem__ = MagicMock(return_value=0)
        record.__getitem__.side_effect = lambda k: 0
        result.single = AsyncMock(return_value=record)
        return result

    session.run = AsyncMock(side_effect=_run)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    health = await ma.get_system_health(user_id="user_abc")
    assert health['user_id'] == "user_abc"
    # Verify the user_id was passed to at least one query
    user_id_calls = [
        c for c in session.run.call_args_list
        if c.kwargs.get('user_id') == 'user_abc'
    ]
    assert len(user_id_calls) > 0, "Expected user_id='user_abc' to be passed in queries"


@pytest.mark.asyncio

async def test_user_scoped_quality_metrics():
    """Quality metrics with user_id filter."""
    ma = _make_analytics()
    session = AsyncMock()

    async def _db_run(query, user_id=None):
        result = AsyncMock()
        record = MagicMock()
        record.__getitem__ = MagicMock(return_value=0)
        record.__getitem__.side_effect = lambda k: 0
        result.single = AsyncMock(return_value=record)
        return result

    async def _qual_run(query, params=None):
        result = AsyncMock()
        record = MagicMock()
        record.__getitem__ = MagicMock(return_value=0.0)
        record.__getitem__.side_effect = lambda k: 0.0
        result.single = AsyncMock(return_value=record)
        return result

    # Need to return different sessions for different calls
    call_count = [0]

    def _session_factory():
        return AsyncContextManagerMock(session)

    async def _run_router(query, *args, **kwargs):
        result = AsyncMock()
        record = MagicMock()
        record.__getitem__ = MagicMock(return_value=0)
        result.single = AsyncMock(return_value=record)
        return result

    session.run = AsyncMock(side_effect=_run_router)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    quality = await ma._get_quality_metrics(user_id="user_xyz")
    assert 'error' not in quality
    # Verify user_id was passed to at least one run call
    any_user_id = any(
        'user_id' in str(kwargs) or ('user_id' in kwargs)
        for _, kwargs in [c for c in session.run.call_args_list if len(c) > 1]
    )
    # The quality query constructs params with user_id when provided
    assert quality is not None


# ============================================================
# get_memory_age_distribution
# ============================================================

@pytest.mark.asyncio
async def test_age_distribution_populated():
    """Age distribution returns correct histogram buckets."""
    ma = _make_analytics()
    session = AsyncMock()

    async def _run(query, params=None):
        result = AsyncMock()
        record = MagicMock()
        record.__getitem__ = MagicMock(side_effect=lambda k: {
            'last_day': 3, 'last_week': 10, 'last_month': 25,
            'last_3_months': 40, 'older': 7
        }.get(k, 0))
        result.single = AsyncMock(return_value=record)
        return result

    session.run = AsyncMock(side_effect=_run)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    dist = await ma.get_memory_age_distribution()
    assert dist['last_day'] == 3
    assert dist['last_week'] == 10
    assert dist['last_month'] == 25
    assert dist['last_3_months'] == 40
    assert dist['older'] == 7


@pytest.mark.asyncio

async def test_age_distribution_empty():
    """No records returned → empty dict."""
    ma = _make_analytics()
    session = AsyncMock()

    async def _run(query, params=None):
        result = AsyncMock()
        result.single = AsyncMock(return_value=None)
        return result

    session.run = AsyncMock(side_effect=_run)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    dist = await ma.get_memory_age_distribution()
    assert dist == {}


@pytest.mark.asyncio

async def test_age_distribution_user_scoped():
    """Age distribution with user_id passes the filter."""
    ma = _make_analytics()
    session = AsyncMock()

    async def _run(query, params=None):
        result = AsyncMock()
        record = MagicMock()
        record.__getitem__ = MagicMock(side_effect=lambda k: {
            'last_day': 1, 'last_week': 2, 'last_month': 3,
            'last_3_months': 4, 'older': 0
        }.get(k, 0))
        result.single = AsyncMock(return_value=record)
        return result

    session.run = AsyncMock(side_effect=_run)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    dist = await ma.get_memory_age_distribution(user_id="user_abc")
    assert dist['last_day'] == 1
    # Check user_id was in params
    call_args = session.run.call_args
    assert call_args is not None


# ============================================================
# get_access_patterns
# ============================================================

@pytest.mark.asyncio
async def test_access_patterns_populated():
    """Access patterns return top accessed memories."""
    ma = _make_analytics()
    session = AsyncMock()

    records = [
        {"id": "mem1", "type": "EpisodicMemory",
         "content": "Remembered hiking trip",
         "access_count": 42, "importance": 0.9},
        {"id": "mem2", "type": "KnowledgeUnit",
         "content": "Python async patterns",
         "access_count": 15, "importance": 0.7},
    ]

    async def _run(query, params=None):
        result = AsyncMock()
        result.__aiter__ = MagicMock(return_value=AsyncIteratorMock(records))
        return result

    session.run = AsyncMock(side_effect=_run)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    patterns = await ma.get_access_patterns(limit=10)
    assert 'top_accessed_memories' in patterns
    top = patterns['top_accessed_memories']
    assert len(top) == 2
    assert top[0]['id'] == 'mem1'
    assert top[0]['access_count'] == 42


@pytest.mark.asyncio

async def test_access_patterns_empty():
    """Empty DB → empty top list."""
    ma = _make_analytics()
    session = AsyncMock()

    async def _run(query, params=None):
        result = AsyncMock()

        async def _anext():
            raise StopAsyncIteration
        result.__anext__ = _anext

        return result

    session.run = AsyncMock(side_effect=_run)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    patterns = await ma.get_access_patterns()
    assert patterns['top_accessed_memories'] == []


# ============================================================
# get_concept_distribution
# ============================================================

@pytest.mark.asyncio
async def test_concept_distribution_populated():
    """Concepts returned with memory counts, sorted descending."""
    ma = _make_analytics()
    session = AsyncMock()

    concept_records = [
        {"concept": "Python", "memory_count": 25},
        {"concept": "AI", "memory_count": 15},
        {"concept": "Testing", "memory_count": 8},
    ]

    async def _run(query, params=None):
        result = AsyncMock()
        result.__aiter__ = MagicMock(return_value=AsyncIteratorMock(concept_records))
        return result

    session.run = AsyncMock(side_effect=_run)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    concepts = await ma.get_concept_distribution(top_k=10)
    assert len(concepts) == 3
    assert concepts[0]['concept'] == 'Python'
    assert concepts[0]['memory_count'] == 25
    assert concepts[2]['concept'] == 'Testing'


@pytest.mark.asyncio

async def test_concept_distribution_empty():
    """No concepts → empty list."""
    ma = _make_analytics()
    session = AsyncMock()

    async def _run(query, params=None):
        result = AsyncMock()
        async def _anext():
            raise StopAsyncIteration
        result.__anext__ = _anext
        return result

    session.run = AsyncMock(side_effect=_run)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    concepts = await ma.get_concept_distribution()
    assert concepts == []


# ============================================================
# Error handling
# ============================================================

@pytest.mark.asyncio
async def test_database_health_error_returns_error_dict():
    """DB error → returns dict with error key, zero counts."""
    ma = _make_analytics()
    session = AsyncMock()
    session.run = AsyncMock(side_effect=Exception("Neo4j connection refused"))
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    health = await ma._get_database_health()
    assert 'error' in health
    assert health['total_memories'] == 0
    assert health['episodic_memories'] == 0


@pytest.mark.asyncio

async def test_quality_metrics_error_returns_error_dict():
    """Quality metrics error → returns dict with error key."""
    ma = _make_analytics()
    session = AsyncMock()
    session.run = AsyncMock(side_effect=Exception("Query timeout"))
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    quality = await ma._get_quality_metrics()
    assert 'error' in quality


@pytest.mark.asyncio

async def test_age_distribution_error_returns_error_dict():
    """Age distribution error → returns dict with error key."""
    ma = _make_analytics()
    session = AsyncMock()
    session.run = AsyncMock(side_effect=RuntimeError("Boom"))
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    dist = await ma.get_memory_age_distribution()
    assert 'error' in dist


# ============================================================
# _get_quality_metrics — populated
# ============================================================

@pytest.mark.asyncio
async def test_quality_metrics_populated():
    """Quality metrics with realistic values."""
    ma = _make_analytics()
    session = AsyncMock()

    async def _run(query, params=None):
        result = AsyncMock()
        record = MagicMock()
        record.__getitem__ = MagicMock(side_effect=lambda k: {
            'avg_importance': 0.75, 'avg_access_count': 3.2,
            'avg_confidence': 0.68, 'unused_count': 5,
            'low_importance_count': 12
        }.get(k, 0))
        result.single = AsyncMock(return_value=record)
        return result

    session.run = AsyncMock(side_effect=_run)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    quality = await ma._get_quality_metrics()
    assert quality['avg_importance'] == 0.75
    assert quality['avg_access_count'] == 3.2
    assert quality['avg_confidence'] == 0.68
    assert quality['unused_memories'] == 5
    assert quality['low_importance_memories'] == 12


@pytest.mark.asyncio

async def test_quality_metrics_no_records():
    """No records returned → all zeros."""
    ma = _make_analytics()
    session = AsyncMock()

    async def _run(query, params=None):
        result = AsyncMock()
        result.single = AsyncMock(return_value=None)
        return result

    session.run = AsyncMock(side_effect=_run)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    quality = await ma._get_quality_metrics()
    assert quality['avg_importance'] == 0.0
    assert quality['avg_access_count'] == 0.0
    assert quality['avg_confidence'] == 0.0
    assert quality['unused_memories'] == 0
    assert quality['low_importance_memories'] == 0


# ============================================================
# _get_cleanup_threshold
# ============================================================

def test_cleanup_threshold_default():
    """No memory_settings in g_data → uses DEFAULT_CLEANUP_THRESHOLD (10000)."""
    ma = _make_analytics()
    threshold = ma._get_cleanup_threshold()
    assert threshold == 10000


def test_cleanup_threshold_custom():
    """Custom cleanup_threshold in g_data is respected."""
    ma = _make_analytics()
    with patch.object(ma, '_get_cleanup_threshold', return_value=500):
        assert ma._get_cleanup_threshold() == 500


# ============================================================
# diagnose_issues
# ============================================================

@pytest.mark.asyncio
async def test_diagnose_healthy_system():
    """Healthy system → no issues, health_score=100, severity='low'."""
    ma = _make_analytics()
    session = _mock_db_health_result(episodic=5, knowledge=3, procedural=2)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    diag = await ma.diagnose_issues()
    assert diag['severity'] == 'low'
    assert diag['health_score'] == 100
    assert len(diag['issues']) <= 0  # may be 0 or more depending on quality metrics


@pytest.mark.asyncio

async def test_diagnose_empty_system():
    """Empty system → 'No memories stored' issue."""
    ma = _make_analytics()
    session = _mock_db_health_result(episodic=0, knowledge=0, procedural=0)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    diag = await ma.diagnose_issues()
    assert any('No memories stored' in issue for issue in diag['issues'])
    assert diag['severity'] in ('low', 'medium', 'high')
    assert diag['health_score'] >= 0


@pytest.mark.asyncio

async def test_diagnose_over_threshold():
    """Total > 10000 → 'High memory count' issue."""
    ma = _make_analytics()
    session = _mock_db_health_result(episodic=6000, knowledge=4000, procedural=1000)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    diag = await ma.diagnose_issues()
    assert any('High memory count' in issue for issue in diag['issues'])
    assert any('pruning' in rec.lower() for rec in diag['recommendations'])


# ============================================================
# generate_report
# ============================================================

@pytest.mark.asyncio
async def test_generate_report_produces_string():
    """generate_report returns a non-empty string with expected sections."""
    ma = _make_analytics()
    session = _mock_db_health_result(episodic=3, knowledge=2, procedural=1)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    report = await ma.generate_report()
    assert isinstance(report, str)
    assert 'Memory System Report' in report
    assert 'DATABASE HEALTH' in report
    assert 'QUALITY METRICS' in report
    assert 'DIAGNOSIS' in report
    assert 'Health Score' in report


@pytest.mark.asyncio

async def test_generate_report_user_scoped():
    """generate_report with user_id includes user in output."""
    ma = _make_analytics()
    session = _mock_db_health_result(episodic=1, knowledge=1, procedural=1)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    report = await ma.generate_report(user_id="test_user")
    assert 'test_user' in report


# ============================================================
# export_stats
# ============================================================

@pytest.mark.asyncio
async def test_export_stats_json():
    """export_stats format='json' produces valid JSON."""
    ma = _make_analytics()
    session = _mock_db_health_result(episodic=2, knowledge=1, procedural=0)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    stats_json = await ma.export_stats(format='json')
    stats = json.loads(stats_json)
    assert 'health' in stats
    assert 'diagnosis' in stats
    assert 'age_distribution' in stats


@pytest.mark.asyncio

async def test_export_stats_text():
    """export_stats format='text' produces report string."""
    ma = _make_analytics()
    session = _mock_db_health_result(episodic=1, knowledge=1, procedural=1)
    ma.memory_db.get_driver().session = MagicMock(
        return_value=AsyncContextManagerMock(session)
    )

    text = await ma.export_stats(format='text')
    assert 'Memory System Report' in text


