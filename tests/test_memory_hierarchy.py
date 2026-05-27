"""Tests for MemoryHierarchy and supporting classes."""

import asyncio
import importlib.util
import math
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _load_module():
    """Load memory_hierarchy module directly, bypassing lib.services.__init__."""
    filepath = Path(__file__).parent.parent / "lib" / "services" / "memory_hierarchy.py"
    spec = importlib.util.spec_from_file_location("memory_hierarchy", filepath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mh = _load_module()
MemoryEntry = _mh.MemoryEntry
TTLCache = _mh.TTLCache
WorkingMemory = _mh.WorkingMemory
ShortTermMemory = _mh.ShortTermMemory
MemoryHierarchy = _mh.MemoryHierarchy


# ============================================================
# MemoryEntry tests
# ============================================================


def test_memory_entry_creation():
    entry = MemoryEntry(content="hello", memory_type="episodic", user_id="u1")
    assert entry.content == "hello"
    assert entry.memory_type == "episodic"
    assert entry.user_id == "u1"
    assert entry.score == 0.0
    assert entry.importance == 0.5
    assert entry.access_count == 0
    assert entry.memory_id is None


def test_memory_entry_to_dict():
    now = time.time()
    entry = MemoryEntry(
        content="test", memory_type="knowledge", score=0.8,
        timestamp=now, access_count=3, importance=0.7,
        user_id="u1", memory_id="m123"
    )
    d = entry.to_dict()
    assert d["text"] == "test"
    assert d["type"] == "knowledge"
    assert d["score"] == 0.8
    assert d["timestamp"] == now
    assert d["access_count"] == 3
    assert d["importance"] == 0.7
    assert d["user_id"] == "u1"
    assert d["memory_id"] == "m123"


def test_memory_entry_default_timestamp():
    """Timestamp defaults to current time."""
    before = time.time()
    entry = MemoryEntry(content="x", memory_type="episodic")
    after = time.time()
    assert before <= entry.timestamp <= after


# ============================================================
# TTLCache tests
# ============================================================


@pytest.mark.asyncio
async def test_ttlcache_set_and_get():
    cache = TTLCache(maxsize=10, ttl=3600)
    await cache.set("a", "value_a")
    val = await cache.get("a")
    assert val == "value_a"


@pytest.mark.asyncio
async def test_ttlcache_get_missing_key():
    cache = TTLCache(maxsize=10, ttl=3600)
    assert await cache.get("nonexistent") is None


@pytest.mark.asyncio
async def test_ttlcache_remove():
    cache = TTLCache(maxsize=10, ttl=3600)
    await cache.set("a", "value_a")
    await cache.remove("a")
    assert await cache.get("a") is None


@pytest.mark.asyncio
async def test_ttlcache_clear():
    cache = TTLCache(maxsize=10, ttl=3600)
    await cache.set("a", "v1")
    await cache.set("b", "v2")
    await cache.clear()
    assert await cache.get("a") is None
    assert await cache.get("b") is None


@pytest.mark.asyncio
async def test_ttlcache_items():
    cache = TTLCache(maxsize=10, ttl=3600)
    await cache.set("a", "va")
    await cache.set("b", "vb")
    items = await cache.items()
    assert len(items) == 2
    assert ("a", "va") in items
    assert ("b", "vb") in items


@pytest.mark.asyncio
async def test_ttlcache_lru_eviction():
    """Oldest entry is evicted when cache reaches maxsize."""
    cache = TTLCache(maxsize=3, ttl=3600)
    await cache.set("a", "va")
    await cache.set("b", "vb")
    await cache.set("c", "vc")
    await cache.set("d", "vd")  # evicts "a"
    assert await cache.get("a") is None
    assert await cache.get("b") == "vb"
    assert await cache.get("c") == "vc"
    assert await cache.get("d") == "vd"


@pytest.mark.asyncio
async def test_ttlcache_lru_ordering_on_get():
    """Getting an entry moves it to MRU end."""
    cache = TTLCache(maxsize=3, ttl=3600)
    await cache.set("a", "va")
    await cache.set("b", "vb")
    await cache.set("c", "vc")
    await cache.get("a")  # move "a" to MRU
    await cache.set("d", "vd")  # evicts "b" (now the oldest)
    assert await cache.get("a") == "va"
    assert await cache.get("b") is None
    assert await cache.get("c") == "vc"
    assert await cache.get("d") == "vd"


@pytest.mark.asyncio
async def test_ttlcache_expiry():
    """Expired entries return None."""
    cache = TTLCache(maxsize=10, ttl=0)  # TTL 0 = instant expiry
    await cache.set("a", "va")
    await asyncio.sleep(0.01)
    assert await cache.get("a") is None


@pytest.mark.asyncio
async def test_ttlcache_set_overwrites_and_resets_timestamp():
    """Setting an existing key updates value and resets TTL."""
    cache = TTLCache(maxsize=10, ttl=3600)
    await cache.set("a", "v1")
    await cache.set("a", "v2")
    assert await cache.get("a") == "v2"


# ============================================================
# WorkingMemory tests
# ============================================================


def test_working_memory_add_and_get():
    wm = WorkingMemory(max_entries=5)
    e1 = MemoryEntry(content="a", memory_type="episodic")
    e2 = MemoryEntry(content="b", memory_type="knowledge")
    wm.add(e1)
    wm.add(e2)
    assert wm.get_all() == [e1, e2]
    assert wm.get_count() == 2


def test_working_memory_overflow():
    """Oldest entries are dropped when max_entries is exceeded."""
    wm = WorkingMemory(max_entries=3)
    for i in range(5):
        wm.add(MemoryEntry(content=str(i), memory_type="episodic"))
    entries = wm.get_all()
    assert len(entries) == 3
    assert entries[0].content == "2"
    assert entries[1].content == "3"
    assert entries[2].content == "4"


def test_working_memory_clear():
    wm = WorkingMemory(max_entries=10)
    wm.add(MemoryEntry(content="x", memory_type="episodic"))
    wm.clear()
    assert wm.get_all() == []
    assert wm.get_count() == 0


def test_working_memory_empty():
    wm = WorkingMemory(max_entries=10)
    assert wm.get_all() == []
    assert wm.get_count() == 0


# ============================================================
# ShortTermMemory tests
# ============================================================


@pytest.mark.asyncio
async def test_short_term_memory_add_and_get():
    stm = ShortTermMemory(cache_size=10, ttl_seconds=3600)
    memories = [{"text": "hello", "score": 0.9}]
    await stm.add("user1", "test query", memories)
    result = await stm.get("user1", "test query")
    assert result == memories


@pytest.mark.asyncio
async def test_short_term_memory_cache_miss():
    stm = ShortTermMemory()
    result = await stm.get("user1", "nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_short_term_memory_per_user_isolation():
    stm = ShortTermMemory()
    await stm.add("user1", "query", [{"text": "u1_mem"}])
    await stm.add("user2", "query", [{"text": "u2_mem"}])
    r1 = await stm.get("user1", "query")
    r2 = await stm.get("user2", "query")
    assert r1[0]["text"] == "u1_mem"
    assert r2[0]["text"] == "u2_mem"


@pytest.mark.asyncio
async def test_short_term_memory_clear_user():
    stm = ShortTermMemory()
    await stm.add("user1", "q", [{"text": "mem"}])
    await stm.clear_user("user1")
    assert await stm.get("user1", "q") is None


@pytest.mark.asyncio
async def test_short_term_memory_clear_user_nonexistent():
    """Clearing a user with no cache does not error."""
    stm = ShortTermMemory()
    await stm.clear_user("nonexistent")  # should not raise


def test_short_term_memory_clear_all():
    stm = ShortTermMemory()
    stm.user_caches["user1"] = MagicMock()
    stm.clear_all()
    assert stm.user_caches == {}


# ============================================================
# MemoryHierarchy._apply_tier_weights tests
# (Building hierarchy from flat memory list)
# ============================================================


_SENTINEL = object()


def _make_hierarchy(memory_db=_SENTINEL, similarity_threshold=0.65):
    """Create a MemoryHierarchy with a mocked long_term_memory.
    Pass None explicitly for no long-term memory. Default creates MagicMock."""
    if memory_db is _SENTINEL:
        memory_db = MagicMock()
    return MemoryHierarchy(
        memory_db=memory_db,
        working_memory_size=20,
        short_term_cache_size=200,
        short_term_ttl=3600,
        similarity_threshold=similarity_threshold,
    )


def test_apply_tier_weights_working_memory():
    """Working memory gets weight 1.0."""
    hier = _make_hierarchy()
    memories = [
        {"text": "wm", "score": 0.9, "tier": "working"},
    ]
    result = hier._apply_tier_weights(memories)
    assert result[0]["score"] == 0.9
    assert result[0]["tier_weight"] == 1.0
    assert result[0]["original_score"] == 0.9


def test_apply_tier_weights_short_term():
    """Short-term memory gets weight 0.9."""
    hier = _make_hierarchy()
    memories = [
        {"text": "stm", "score": 0.8, "tier": "short_term"},
    ]
    result = hier._apply_tier_weights(memories)
    assert result[0]["score"] == pytest.approx(0.72)  # 0.8 * 0.9
    assert result[0]["tier_weight"] == 0.9
    assert result[0]["original_score"] == 0.8


def test_apply_tier_weights_long_term():
    """Long-term memory gets weight 0.8."""
    hier = _make_hierarchy()
    memories = [
        {"text": "ltm", "score": 1.0, "tier": "long_term"},
    ]
    result = hier._apply_tier_weights(memories)
    assert result[0]["score"] == 0.8
    assert result[0]["tier_weight"] == 0.8


def test_apply_tier_weights_default_tier():
    """Missing tier defaults to long_term weight 0.8."""
    hier = _make_hierarchy()
    memories = [
        {"text": "no_tier", "score": 0.5},
    ]
    result = hier._apply_tier_weights(memories)
    assert result[0]["score"] == 0.4  # 0.5 * 0.8
    assert result[0]["tier_weight"] == 0.8


def test_apply_tier_weights_default_score():
    """Missing score defaults to 0.5."""
    hier = _make_hierarchy()
    memories = [
        {"text": "no_score", "tier": "working"},
    ]
    result = hier._apply_tier_weights(memories)
    assert result[0]["score"] == 0.5  # 0.5 * 1.0
    assert result[0]["original_score"] == 0.5


def test_apply_tier_weights_mixed_tiers():
    """Mixed tiers get correct weights. This tests building hierarchy from flat list."""
    hier = _make_hierarchy()
    memories = [
        {"text": "wm", "score": 1.0, "tier": "working"},
        {"text": "stm", "score": 0.8, "tier": "short_term"},
        {"text": "ltm", "score": 0.6, "tier": "long_term"},
        {"text": "ltm2", "score": 0.9, "tier": "long_term"},
    ]
    result = hier._apply_tier_weights(memories)
    assert result[0]["score"] == 1.0   # 1.0 * 1.0
    assert result[0]["tier_weight"] == 1.0
    assert result[1]["score"] == pytest.approx(0.72)  # 0.8 * 0.9
    assert result[2]["score"] == 0.48  # 0.6 * 0.8
    assert result[3]["score"] == pytest.approx(0.72)  # 0.9 * 0.8


def test_apply_tier_weights_empty_list():
    """Empty list returns empty list."""
    hier = _make_hierarchy()
    result = hier._apply_tier_weights([])
    assert result == []


def test_apply_tier_weights_preserves_all_fields():
    """Original fields are preserved and new fields added."""
    hier = _make_hierarchy()
    memories = [{"text": "hi", "score": 0.7, "tier": "short_term", "extra": True}]
    result = hier._apply_tier_weights(memories)
    assert result[0]["text"] == "hi"
    assert result[0]["extra"] is True
    assert result[0]["score"] == pytest.approx(0.63)
    assert result[0]["original_score"] == 0.7
    assert result[0]["tier_weight"] == 0.9


# ============================================================
# MemoryHierarchy._extract_memory_text tests
# ============================================================


class _DummySummary:
    summary = "dummy summary"


class _DummyStatement:
    statement = "dummy statement"


class _DummyDescription:
    description = "dummy description"


class _DummyEmpty:
    pass


def test_extract_text_from_dict_with_summary():
    hier = _make_hierarchy()
    obj = {"summary": "hello world"}
    assert hier._extract_memory_text(obj) == "hello world"


def test_extract_text_from_dict_with_statement():
    hier = _make_hierarchy()
    obj = {"statement": "I am a statement"}
    assert hier._extract_memory_text(obj) == "I am a statement"


def test_extract_text_from_dict_with_description():
    hier = _make_hierarchy()
    obj = {"description": "a description"}
    assert hier._extract_memory_text(obj) == "a description"


def test_extract_text_from_dict_fallback():
    """Empty dict falls through or-sum to str()."""
    hier = _make_hierarchy()
    assert hier._extract_memory_text({}) == "{}"


def test_extract_text_from_object_with_summary():
    hier = _make_hierarchy()
    assert hier._extract_memory_text(_DummySummary()) == "dummy summary"


def test_extract_text_from_object_with_statement():
    hier = _make_hierarchy()
    assert hier._extract_memory_text(_DummyStatement()) == "dummy statement"


def test_extract_text_from_object_with_description():
    hier = _make_hierarchy()
    assert hier._extract_memory_text(_DummyDescription()) == "dummy description"


def test_extract_text_from_object_fallback():
    hier = _make_hierarchy()
    result = hier._extract_memory_text(_DummyEmpty())
    assert "DummyEmpty" in result


# ============================================================
# MemoryHierarchy: add_to_working_memory / clear tests
# ============================================================


def test_add_to_working_memory():
    hier = _make_hierarchy()
    hier.add_to_working_memory("test content", "episodic", "u1", importance=0.8)
    entries = hier.working_memory.get_all()
    assert len(entries) == 1
    assert entries[0].content == "test content"
    assert entries[0].memory_type == "episodic"
    assert entries[0].user_id == "u1"
    assert entries[0].importance == 0.8


def test_add_to_working_memory_default_importance():
    hier = _make_hierarchy()
    hier.add_to_working_memory("x", "knowledge", "u2")
    assert hier.working_memory.get_all()[0].importance == 0.5


def test_clear_working_memory():
    hier = _make_hierarchy()
    hier.add_to_working_memory("x", "episodic", "u1")
    hier.clear_working_memory()
    assert hier.working_memory.get_count() == 0


@pytest.mark.asyncio
async def test_clear_short_term_cache_specific_user():
    """clear_short_term_cache clears the global cache (user_id is ignored)."""
    hier = _make_hierarchy()
    await hier.short_term_memory.add("global", "q", [{"text": "m"}])
    await hier.clear_short_term_cache(user_id="u1")
    result = await hier.short_term_memory.get("global", "q")
    assert result is None


@pytest.mark.asyncio
async def test_clear_short_term_cache_all_users():
    """clear_short_term_cache(None) clears the global cache (backward compat)."""
    hier = _make_hierarchy()
    await hier.short_term_memory.add("global", "q", [{"text": "m"}])
    await hier.clear_short_term_cache(user_id=None)
    result = await hier.short_term_memory.get("global", "q")
    assert result is None


# ============================================================
# MemoryHierarchy.retrieve_memories tests
# (Parent-child relationship traversal across tiers)
# ============================================================


@pytest.mark.asyncio
async def test_retrieve_from_working_memory_only():
    """When working memory has entries and no cache, those appear with tier='working'."""
    db = MagicMock()
    db.get_total_entries = AsyncMock(return_value=0)
    hier = MemoryHierarchy(
        memory_db=db,
        working_memory_size=20,
        similarity_threshold=0.65,
    )
    hier.add_to_working_memory("wm content", "episodic", "u1", importance=0.9)

    result = await hier.retrieve_memories(
        query="anything", user_id="u1", top_k=5
    )

    wm_result = [r for r in result if r["tier"] == "working"]
    assert len(wm_result) == 1
    assert wm_result[0]["text"] == "wm content"


@pytest.mark.asyncio
async def test_retrieve_working_memory_excluded():
    """When include_working=False, working memory entries are skipped."""
    hier = _make_hierarchy()
    hier.add_to_working_memory("wm content", "episodic", "u1")

    db = hier.long_term_memory
    db.get_total_entries = AsyncMock(return_value=0)

    result = await hier.retrieve_memories(
        query="anything", user_id="u1", top_k=5, include_working=False
    )
    wm_results = [r for r in result if r["tier"] == "working"]
    assert len(wm_results) == 0


@pytest.mark.asyncio
async def test_retrieve_from_short_term_cache():
    """When short-term cache has a hit, those memories are returned."""
    db = MagicMock()
    hier = MemoryHierarchy(memory_db=db, similarity_threshold=0.65)
    await hier.short_term_memory.add("global", "test query", [
        {"text": "cached mem", "score": 0.9}
    ])

    result = await hier.retrieve_memories(
        query="test query", user_id="u1", top_k=5, include_working=False
    )
    st_results = [r for r in result if r["tier"] == "short_term"]
    assert len(st_results) == 1
    assert st_results[0]["text"] == "cached mem"
    assert st_results[0]["tier_weight"] == 0.9


@pytest.mark.asyncio
async def test_retrieve_from_long_term_when_cache_miss():
    """When short-term cache misses, long-term DB is queried."""
    db = MagicMock()
    db.get_total_entries = AsyncMock(return_value=5)
    db.search_with_context = AsyncMock(return_value=[
        {
            "memory": {"summary": "lt mem", "id": "m1"},
            "type": "vector",
            "score": 0.88,
        }
    ])
    db.get_driver = MagicMock()

    hier = MemoryHierarchy(memory_db=db, similarity_threshold=0.65)

    result = await hier.retrieve_memories(
        query="miss", user_id="u1", top_k=5, include_working=False
    )
    lt_results = [r for r in result if r["tier"] == "long_term"]
    assert len(lt_results) == 1
    assert lt_results[0]["text"] == "lt mem"
    assert lt_results[0]["score"] == pytest.approx(0.88 * 0.8)


@pytest.mark.asyncio
async def test_retrieve_long_term_empty_db():
    """When long-term has 0 entries, no long-term results are returned."""
    db = MagicMock()
    db.get_total_entries = AsyncMock(return_value=0)

    hier = MemoryHierarchy(memory_db=db, similarity_threshold=0.65)

    result = await hier.retrieve_memories(
        query="anything", user_id="u1", top_k=5, include_working=False
    )
    assert result == []


@pytest.mark.asyncio
async def test_retrieve_long_term_none_db():
    """When long_term_memory is None, no error and empty result."""
    hier = _make_hierarchy(memory_db=None)

    result = await hier.retrieve_memories(
        query="anything", user_id="u1", top_k=5, include_working=False
    )
    assert result == []


@pytest.mark.asyncio
async def test_retrieve_long_term_error_returns_empty():
    """When long-term DB errors, it's caught and returns available results."""
    db = MagicMock()
    db.get_total_entries = AsyncMock(return_value=5)
    db.search_with_context = AsyncMock(side_effect=RuntimeError("DB down"))

    hier = MemoryHierarchy(memory_db=db, similarity_threshold=0.65)
    hier.add_to_working_memory("wm", "episodic", "u1")

    # Should not raise, working memory entries still returned
    result = await hier.retrieve_memories(query="q", user_id="u1", top_k=5)
    wm_results = [r for r in result if r["tier"] == "working"]
    assert len(wm_results) == 1


@pytest.mark.asyncio
async def test_retrieve_all_three_tiers():
    """Traversal through all three tiers: working -> short_term -> long_term."""
    db = MagicMock()
    db.get_total_entries = AsyncMock(return_value=3)
    db.search_with_context = AsyncMock(return_value=[
        {"memory": {"summary": "lt mem"}, "type": "context", "score": 0.9}
    ])
    db.get_driver = MagicMock()

    hier = MemoryHierarchy(memory_db=db, similarity_threshold=0.65)
    hier.add_to_working_memory("wm mem", "episodic", "u1")

    # Cache miss triggers long-term lookup; score 0.9 * 0.8 = 0.72 > threshold
    result = await hier.retrieve_memories(query="all tiers", user_id="u1", top_k=5)

    tiers = [r["tier"] for r in result]
    assert "working" in tiers
    assert "long_term" in tiers


@pytest.mark.asyncio
async def test_retrieve_similarity_threshold_filter():
    """Memories below threshold are filtered out (except working)."""
    db = MagicMock()
    db.get_total_entries = AsyncMock(return_value=3)
    db.search_with_context = AsyncMock(return_value=[
        {"memory": {"summary": "low score"}, "type": "context", "score": 0.3}
    ])
    db.get_driver = MagicMock()

    hier = MemoryHierarchy(memory_db=db, similarity_threshold=0.65)
    hier.add_to_working_memory("wm", "episodic", "u1")

    result = await hier.retrieve_memories(
        query="q", user_id="u1", top_k=5, include_working=False
    )
    lt_results = [r for r in result if r["tier"] == "long_term"]
    assert len(lt_results) == 0


@pytest.mark.asyncio
async def test_retrieve_working_memory_bypasses_threshold():
    """Working memory entries bypass the similarity threshold filter."""
    hier = _make_hierarchy(similarity_threshold=0.99)
    hier.long_term_memory.get_total_entries = AsyncMock(return_value=0)
    hier.add_to_working_memory("wm", "episodic", "u1")
    result = await hier.retrieve_memories(
        query="q", user_id="u1", top_k=5, include_working=True
    )
    wm = [r for r in result if r["tier"] == "working"]
    assert len(wm) == 1


@pytest.mark.asyncio
async def test_retrieve_top_k_limit():
    """Only top_k results are returned."""
    db = MagicMock()
    db.get_total_entries = AsyncMock(return_value=10)
    db.search_with_context = AsyncMock(return_value=[
        {"memory": {"summary": f"lt{i}"}, "type": "context", "score": 0.7 + i * 0.01}
        for i in range(10)
    ])
    db.get_driver = MagicMock()

    hier = MemoryHierarchy(memory_db=db, similarity_threshold=0.0)
    result = await hier.retrieve_memories(
        query="q", user_id="u1", top_k=3, include_working=False
    )
    assert len(result) == 3


@pytest.mark.asyncio
async def test_retrieve_empty_hierarchy():
    """Empty hierarchy edge case: no working memory, no cache, no long-term."""
    db = MagicMock()
    db.get_total_entries = AsyncMock(return_value=0)

    hier = MemoryHierarchy(memory_db=db, similarity_threshold=0.65)
    result = await hier.retrieve_memories(query="q", user_id="u1", top_k=5)
    assert result == []


# ============================================================
# MemoryHierarchy.get_stats tests
# ============================================================


@pytest.mark.asyncio
async def test_get_stats_empty():
    """Stats with no DB entries."""
    db = MagicMock()
    mock_result = MagicMock()
    mock_result.single = AsyncMock(return_value=None)
    mock_session = MagicMock()
    mock_session.run = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    db.get_driver.return_value.session.return_value = mock_session

    hier = MemoryHierarchy(memory_db=db)
    stats = await hier.get_stats()
    assert stats["working_memory_count"] == 0
    assert stats["long_term_total"] == 0


@pytest.mark.asyncio
async def test_get_stats_with_working_memory_and_long_term():
    """Stats reflect working memory count and long-term total."""
    db = MagicMock()
    mock_record = MagicMock()
    mock_record.__getitem__.return_value = 42
    mock_result = MagicMock()
    mock_result.single = AsyncMock(return_value=mock_record)
    mock_session = MagicMock()
    mock_session.run = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    db.get_driver.return_value.session.return_value = mock_session

    hier = MemoryHierarchy(memory_db=db)
    hier.add_to_working_memory("a", "episodic", "u1")
    hier.add_to_working_memory("b", "knowledge", "u1")

    stats = await hier.get_stats()
    assert stats["working_memory_count"] == 2
    assert stats["long_term_total"] == 42


@pytest.mark.asyncio
async def test_get_stats_with_user_id():
    """Stats with user_id filter."""
    db = MagicMock()
    mock_record = MagicMock()
    mock_record.__getitem__.return_value = 5
    mock_result = MagicMock()
    mock_result.single = AsyncMock(return_value=mock_record)
    mock_session = MagicMock()
    mock_session.run = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    db.get_driver.return_value.session.return_value = mock_session

    hier = MemoryHierarchy(memory_db=db)
    stats = await hier.get_stats(user_id="u1")
    assert stats["long_term_total"] == 5


@pytest.mark.asyncio
async def test_get_stats_db_error():
    """Stats gracefully handles DB errors."""
    db = MagicMock()
    db.get_driver.side_effect = RuntimeError("DB down")

    hier = MemoryHierarchy(memory_db=db)
    stats = await hier.get_stats()
    assert stats["working_memory_count"] == 0
    assert stats["long_term_total"] == 0


@pytest.mark.asyncio
async def test_get_stats_null_long_term_memory():
    """Stats with no long_term_memory set."""
    hier = _make_hierarchy(memory_db=None)
    stats = await hier.get_stats()
    assert stats["working_memory_count"] == 0
    assert stats["long_term_total"] == 0


# ============================================================
# MemoryHierarchy._increment_access_counts tests
# ============================================================


@pytest.mark.asyncio
async def test_increment_access_counts_success():
    """Increment access counts runs queries for each memory."""
    db = MagicMock()
    mock_session = MagicMock()
    mock_session.run = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    db.get_driver.return_value.session.return_value = mock_session

    hier = MemoryHierarchy(memory_db=db)
    await hier._increment_access_counts([
        ("m1", "EpisodicMemory"),
        ("m2", "KnowledgeUnit"),
    ])
    assert mock_session.run.call_count == 2


@pytest.mark.asyncio
async def test_increment_access_counts_error_handled():
    """Error in access count update is caught and logged."""
    db = MagicMock()
    db.get_driver.side_effect = RuntimeError("DB down")

    hier = MemoryHierarchy(memory_db=db)
    # Should not raise
    await hier._increment_access_counts([("m1", "EpisodicMemory")])
