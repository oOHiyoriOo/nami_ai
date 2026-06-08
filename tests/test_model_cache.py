"""
Tests for lib/services/model_cache.py

Covers:
- record_success() — new entry, update existing, malformed name
- get_cached_models() — empty, with filter, without filter
- get_model() — found vs not found
- is_cached() — True/False
- get_cache_stats() — empty, with entries, most_used
- clear() — empties cache, persists to disk
- to_ollama_format() — with/without provider filter
- Edge cases: malformed name, corrupted JSON, disk I/O errors
"""

import importlib.util
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


def _load_module():
    """Load model_cache module directly, bypassing lib.services.__init__."""
    filepath = Path(__file__).parent.parent / "lib" / "services" / "model_cache.py"
    spec = importlib.util.spec_from_file_location("model_cache", filepath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mc_module = _load_module()
ModelCache = mc_module.ModelCache
CachedModel = mc_module.CachedModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_cache_file(cache_path: str) -> dict:
    """Read the cache JSON from disk."""
    if not os.path.exists(cache_path):
        return {}
    with open(cache_path, encoding="utf-8") as f:
        return json.load(f)


# ===================================================================
# record_success tests
# ===================================================================

def test_record_success_new_entry():
    """record_success creates a new CachedModel entry."""
    print("Test: record_success creates new entry")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            cache.record_success("openai/gpt-4")
            assert cache.is_cached("openai/gpt-4"), "model should be cached"
            cached = cache.get_model("openai/gpt-4")
            assert cached.provider == "openai"
            assert cached.model == "gpt-4"
            assert cached.success_count == 1
            assert cached.name == "openai/gpt-4"
        on_disk = _read_cache_file(cache_path)
        assert "openai/gpt-4" in on_disk



def test_record_success_update_existing():
    """record_success increments success_count and updates last_used."""
    print("Test: record_success updates existing entry")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            cache.record_success("ollama/llama3")
            cache.record_success("ollama/llama3")
            assert cache.is_cached("ollama/llama3")
            cached = cache.get_model("ollama/llama3")
            assert cached.success_count == 2
            assert cached.last_used >= cached.first_used
        on_disk = _read_cache_file(cache_path)
        assert on_disk["ollama/llama3"]["success_count"] == 2



def test_record_success_malformed_model_name():
    """record_success with no '/' logs warning and returns early."""
    print("Test: record_success malformed model name (no '/')")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            cache.record_success("invalidmodel")
            assert not cache.is_cached("invalidmodel"), "malformed name should not be cached"
        on_disk = _read_cache_file(cache_path)
        assert not on_disk, "nothing should be on disk"



def test_record_success_multiple_providers():
    """record_success handles models from different providers."""
    print("Test: record_success with multiple providers")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            cache.record_success("openai/gpt-4")
            cache.record_success("ollama/llama3")
            cache.record_success("openai/gpt-3.5-turbo")
            assert len(cache.get_cached_models()) == 3
            openai_models = cache.get_cached_models(provider="openai")
            assert len(openai_models) == 2
            ollama_models = cache.get_cached_models(provider="ollama")
            assert len(ollama_models) == 1



# ===================================================================
# get_cached_models tests
# ===================================================================

def test_get_cached_models_empty():
    """get_cached_models returns empty list when cache is empty."""
    print("Test: get_cached_models empty")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            result = cache.get_cached_models()
            assert result == []



def test_get_cached_models_without_filter():
    """get_cached_models without provider returns all models sorted by last_used desc."""
    print("Test: get_cached_models without filter")
    import time
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            cache.record_success("openai/gpt-4")
            time.sleep(0.01)
            cache.record_success("ollama/llama3")
            models = cache.get_cached_models()
            assert len(models) == 2
            assert models[0].name == "ollama/llama3"
            assert models[1].name == "openai/gpt-4"



def test_get_cached_models_with_provider_filter():
    """get_cached_models with provider filter returns only that provider."""
    print("Test: get_cached_models with provider filter")
    import time
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            cache.record_success("openai/gpt-4")
            time.sleep(0.01)
            cache.record_success("ollama/llama3")
            cache.record_success("ollama/mistral")
            ollama_models = cache.get_cached_models(provider="ollama")
            assert len(ollama_models) == 2
            for m in ollama_models:
                assert m.provider == "ollama"
            assert ollama_models[0].last_used >= ollama_models[1].last_used



def test_get_cached_models_provider_filter_no_match():
    """get_cached_models with non-existent provider returns empty list."""
    print("Test: get_cached_models provider filter no match")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            cache.record_success("openai/gpt-4")
            result = cache.get_cached_models(provider="nonexistent")
            assert result == []



# ===================================================================
# get_model tests
# ===================================================================

def test_get_model_found():
    """get_model returns CachedModel when found."""
    print("Test: get_model found")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            cache.record_success("openai/gpt-4")
            model = cache.get_model("openai/gpt-4")
            assert model is not None
            assert model.name == "openai/gpt-4"
            assert model.provider == "openai"
            assert model.model == "gpt-4"



def test_get_model_not_found():
    """get_model returns None when not found."""
    print("Test: get_model not found")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            cache.record_success("openai/gpt-4")
            model = cache.get_model("ollama/llama3")
            assert model is None



# ===================================================================
# is_cached tests
# ===================================================================

def test_is_cached_true():
    """is_cached returns True for cached model."""
    print("Test: is_cached True")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            cache.record_success("openai/gpt-4")
            assert cache.is_cached("openai/gpt-4") is True



def test_is_cached_false():
    """is_cached returns False for uncached model."""
    print("Test: is_cached False")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            assert cache.is_cached("openai/gpt-4") is False



# ===================================================================
# get_cache_stats tests
# ===================================================================

def test_get_cache_stats_empty():
    """get_cache_stats on empty cache returns zeroed stats."""
    print("Test: get_cache_stats empty")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            stats = cache.get_cache_stats()
            assert stats["total_models"] == 0
            assert stats["total_successes"] == 0
            assert stats["providers"] == []
            assert stats["most_used"] is None



def test_get_cache_stats_with_entries():
    """get_cache_stats returns correct counts and most_used."""
    print("Test: get_cache_stats with entries")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            cache.record_success("openai/gpt-4")
            cache.record_success("openai/gpt-4")
            cache.record_success("openai/gpt-4")
            cache.record_success("ollama/llama3")
            cache.record_success("ollama/llama3")
            stats = cache.get_cache_stats()
            assert stats["total_models"] == 2
            assert stats["total_successes"] == 5
            assert set(stats["providers"]) == {"openai", "ollama"}
            assert stats["most_used"]["name"] == "openai/gpt-4"
            assert stats["most_used"]["count"] == 3



def test_get_cache_stats_most_used_single_entry():
    """most_used with a single entry returns that entry."""
    print("Test: get_cache_stats most_used single entry")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            cache.record_success("ollama/llama3")
            stats = cache.get_cache_stats()
            assert stats["most_used"]["name"] == "ollama/llama3"
            assert stats["most_used"]["count"] == 1



# ===================================================================
# clear tests
# ===================================================================

def test_clear_empties_cache():
    """clear removes all entries from in-memory cache."""
    print("Test: clear empties cache")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            cache.record_success("openai/gpt-4")
            cache.record_success("ollama/llama3")
            assert len(cache.get_cached_models()) == 2
            cache.clear()
            assert cache.get_cached_models() == []
            assert cache.get_cache_stats()["total_models"] == 0
        on_disk = _read_cache_file(cache_path)
        assert on_disk == {}



def test_clear_persists_empty_to_disk():
    """clear writes empty dict to disk."""
    print("Test: clear persists empty to disk")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            cache.record_success("openai/gpt-4")
            cache.clear()
        on_disk = _read_cache_file(cache_path)
        assert on_disk == {}



def test_clear_on_already_empty():
    """clear on empty cache is a no-op."""
    print("Test: clear on already empty")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            cache.clear()  # should not raise



# ===================================================================
# to_ollama_format tests
# ===================================================================

def test_to_ollama_format_without_filter():
    """to_ollama_format returns all models in Ollama format."""
    print("Test: to_ollama_format without filter")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            cache.record_success("openai/gpt-4")
            result = cache.to_ollama_format()
            assert len(result) == 1
            assert result[0]["name"] == "openai/gpt-4"
            assert result[0]["size"] == 0
            assert result[0]["digest"] == ""
            assert result[0]["details"]["provider"] == "openai"
            assert result[0]["details"]["model"] == "gpt-4"
            assert result[0]["details"]["success_count"] == 1
            assert "first_used" in result[0]["details"]
            assert "last_used" in result[0]["details"]
            assert "modified_at" in result[0]



def test_to_ollama_format_with_provider_filter():
    """to_ollama_format with provider filter returns only matching models."""
    print("Test: to_ollama_format with provider filter")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            cache.record_success("openai/gpt-4")
            cache.record_success("ollama/llama3")
            result = cache.to_ollama_format(provider="ollama")
            assert len(result) == 1
            assert result[0]["name"] == "ollama/llama3"



def test_to_ollama_format_empty():
    """to_ollama_format on empty cache returns empty list."""
    print("Test: to_ollama_format empty")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            result = cache.to_ollama_format()
            assert result == []



# ===================================================================
# Edge case: Corrupted JSON on load
# ===================================================================

def test_load_from_disk_corrupted_json():
    """_load_from_disk handles corrupted JSON gracefully."""
    print("Test: _load_from_disk corrupted JSON")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write("this is not valid json {{{")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            assert cache.get_cached_models() == [], "corrupted JSON should yield empty cache"



def test_load_from_disk_missing_file():
    """_load_from_disk handles missing cache file gracefully."""
    print("Test: _load_from_disk missing file")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            assert cache.get_cached_models() == []



# ===================================================================
# Edge case: Disk I/O error on save
# ===================================================================

def test_save_to_disk_os_error():
    """_save_to_disk handles OSError gracefully when path is a directory."""
    print("Test: _save_to_disk OSError")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        os.makedirs(cache_path, exist_ok=True)
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            cache.record_success("openai/gpt-4")
            assert cache.is_cached("openai/gpt-4"), "in-memory cache should survive save error"



# ===================================================================
# Edge case: record_success persistence
# ===================================================================

def test_record_success_new_entry_persists():
    """record_success for a new entry writes to disk."""
    print("Test: record_success persists new entry")
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "model_cache.json")
        with patch.object(mc_module, "CACHE_FILE", cache_path):
            cache = ModelCache()
            cache.record_success("openai/gpt-4")
        on_disk = _read_cache_file(cache_path)
        assert "openai/gpt-4" in on_disk
        assert on_disk["openai/gpt-4"]["provider"] == "openai"
        assert on_disk["openai/gpt-4"]["success_count"] == 1



# ===================================================================
# CachedModel dataclass tests
# ===================================================================

def test_cached_model_to_dict():
    """CachedModel.to_dict() returns correct dictionary."""
    print("Test: CachedModel.to_dict()")
    cm = CachedModel(
        name="openai/gpt-4",
        provider="openai",
        model="gpt-4",
        first_used="2024-01-01T00:00:00Z",
        last_used="2024-01-02T00:00:00Z",
        success_count=5
    )
    d = cm.to_dict()
    assert d["name"] == "openai/gpt-4"
    assert d["provider"] == "openai"
    assert d["model"] == "gpt-4"
    assert d["success_count"] == 5



# ===================================================================
# Runner
# ===================================================================
