"""Tests for MemoryDecayService."""

import importlib.util
import math
from pathlib import Path


def _load_module():
    """Load memory_decay module directly, bypassing lib.services.__init__."""
    filepath = Path(__file__).parent.parent / "lib" / "services" / "memory_decay.py"
    spec = importlib.util.spec_from_file_location("memory_decay", filepath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_memdecay = _load_module()
MemoryDecayService = _memdecay.MemoryDecayService
DecayConfig = _memdecay.DecayConfig

HALF_LIFE_DAYS = 365.0
HALF_LIFE_HOURS = HALF_LIFE_DAYS * 24.0
SECONDS_PER_HOUR = 3600.0


def _make_service(half_life_days: float = HALF_LIFE_DAYS) -> MemoryDecayService:
    return MemoryDecayService(DecayConfig(half_life_days=half_life_days))


# ============================================================
# should_prune tests
# ============================================================

NOW_SEC = 1_700_000_000.0


def _make_ts(age_days: float, base_sec: float = NOW_SEC) -> int:
    """Return a creation_timestamp (ms) with the given age in days."""
    creation_sec = base_sec - age_days * 86400.0
    return int(creation_sec * 1000)


def test_prune_all_three_conditions_met():
    """Old + rarely accessed + unimportant → True."""
    svc = MemoryDecayService(DecayConfig())
    ts = _make_ts(366)  # older than max_age_days=365
    assert svc.should_prune(ts, access_count=0, importance=0.1, current_time=NOW_SEC) is True


def test_prune_only_old_but_accessed_frequently():
    """Old but access_count >= min_access_count → False."""
    svc = MemoryDecayService(DecayConfig())
    ts = _make_ts(366)
    assert svc.should_prune(ts, access_count=5, importance=0.1, current_time=NOW_SEC) is False


def test_prune_only_old_but_high_importance():
    """Old but importance >= min_importance → False."""
    svc = MemoryDecayService(DecayConfig())
    ts = _make_ts(366)
    assert svc.should_prune(ts, access_count=0, importance=0.5, current_time=NOW_SEC) is False


def test_prune_old_and_rarely_accessed_but_important():
    """Old + access_count < min, but importance >= min_importance → False."""
    svc = MemoryDecayService(DecayConfig())
    ts = _make_ts(366)
    assert svc.should_prune(ts, access_count=1, importance=0.4, current_time=NOW_SEC) is False


def test_prune_recent_memory_never_prunes():
    """Recent memory (< max_age_days) → False regardless of access/importance."""
    svc = MemoryDecayService(DecayConfig())
    ts = _make_ts(100)
    assert svc.should_prune(ts, access_count=0, importance=0.0, current_time=NOW_SEC) is False


# --- exact threshold edges ---


def test_prune_exact_at_max_age_days():
    """age_days == max_age_days (365) → not old → False (strict > required)."""
    svc = MemoryDecayService(DecayConfig())
    ts = _make_ts(365)
    assert svc.should_prune(ts, access_count=0, importance=0.0, current_time=NOW_SEC) is False


def test_prune_exact_at_min_access_count():
    """access_count == min_access_count (2) → not rarely accessed → False."""
    svc = MemoryDecayService(DecayConfig())
    ts = _make_ts(366)
    assert svc.should_prune(ts, access_count=2, importance=0.0, current_time=NOW_SEC) is False


def test_prune_exact_at_min_importance():
    """importance == min_importance (0.3) → not unimportant → False."""
    svc = MemoryDecayService(DecayConfig())
    ts = _make_ts(366)
    assert svc.should_prune(ts, access_count=0, importance=0.3, current_time=NOW_SEC) is False


# --- custom current_time vs default ---


def test_prune_with_custom_current_time():
    """Explicit current_time parameter is used for age calculation."""
    svc = MemoryDecayService(DecayConfig())
    ts = _make_ts(366, base_sec=NOW_SEC)
    assert svc.should_prune(ts, access_count=0, importance=0.1, current_time=NOW_SEC) is True

    # Same timestamp but with a "now" that makes it recent → False
    future_now = NOW_SEC + 100 * 86400  # 100 days in the future
    ts_future = int((NOW_SEC - 365 * 86400) * 1000)
    # relative to "future_now", the memory is 465 days old
    assert svc.should_prune(ts_future, access_count=0, importance=0.1, current_time=future_now) is True


def test_prune_with_default_current_time():
    """Default current_time=time.time() still produces a valid boolean."""
    svc = MemoryDecayService(DecayConfig())
    # Very old timestamp (epoch) + zero access + zero importance → should prune
    result = svc.should_prune(0, access_count=0, importance=0.0)
    assert isinstance(result, bool)


# --- fresh memory ---


def test_fresh_memory_decay_near_one():
    """Creation timestamp ≈ now → decay factor ≈ 1.0."""
    svc = _make_service()
    now_sec = 1_700_000_000.0
    now_ms = int(now_sec * 1000)
    result = svc.compute_decay_factor(now_ms, current_time=now_sec)
    assert result == 1.0


# --- half-life ---


def test_at_half_life_decay_is_e_negative_one():
    """Age = half_life_days → decay = e^(-1)."""
    svc = _make_service()
    now_sec = 1_700_000_000.0
    creation_sec = now_sec - HALF_LIFE_DAYS * 86400.0
    creation_ms = int(creation_sec * 1000)
    result = svc.compute_decay_factor(creation_ms, current_time=now_sec)
    assert math.isclose(result, math.exp(-1), rel_tol=1e-9)


def test_at_half_life_with_custom_half_life():
    """Custom half_life=30 days; age=30 days → decay = e^(-1)."""
    svc = _make_service(half_life_days=30.0)
    now_sec = 1_700_000_000.0
    creation_sec = now_sec - 30.0 * 86400.0
    creation_ms = int(creation_sec * 1000)
    result = svc.compute_decay_factor(creation_ms, current_time=now_sec)
    assert math.isclose(result, math.exp(-1), rel_tol=1e-9)


# --- very old ---


def test_very_old_memory_decays_to_zero():
    """Age >> half_life → decay ≈ 0.0."""
    svc = _make_service()
    now_sec = 1_700_000_000.0
    creation_sec = now_sec - HALF_LIFE_DAYS * 86400.0 * 100
    creation_ms = int(creation_sec * 1000)
    result = svc.compute_decay_factor(creation_ms, current_time=now_sec)
    # e^(-100) ≈ 3.72e-44 — effectively zero
    assert result < 1e-40


def test_moderately_old_memory():
    """Age = 3 * half_life → decay = e^(-3)."""
    svc = _make_service()
    now_sec = 1_700_000_000.0
    creation_sec = now_sec - HALF_LIFE_DAYS * 86400.0 * 3
    creation_ms = int(creation_sec * 1000)
    result = svc.compute_decay_factor(creation_ms, current_time=now_sec)
    assert math.isclose(result, math.exp(-3), rel_tol=1e-9)


# --- custom current_time ---


def test_default_current_time_uses_time_time():
    """When current_time is omitted, the function uses time.time() internally."""
    svc = _make_service()
    # A timestamp far in the past should still produce a valid decay
    result = svc.compute_decay_factor(0)
    assert 0.0 <= result <= 1.0


# --- edge: future timestamp ---


def test_future_timestamp_clamped_to_one():
    """creation_timestamp in the future → decay_factor = 1.0 (clamped)."""
    svc = _make_service()
    now_sec = 1_700_000_000.0
    future_sec = now_sec + 1000.0  # 1000 seconds in the future
    future_ms = int(future_sec * 1000)
    result = svc.compute_decay_factor(future_ms, current_time=now_sec)
    assert result == 1.0


def test_future_timestamp_clamped_with_small_half_life():
    """Future timestamp still clamped even with short half-life."""
    svc = _make_service(half_life_days=1.0)
    now_sec = 1_700_000_000.0
    future_sec = now_sec + 10.0
    future_ms = int(future_sec * 1000)
    result = svc.compute_decay_factor(future_ms, current_time=now_sec)
    assert result == 1.0


# --- edge: zero / negative age ---


def test_zero_age_clamped_to_one():
    """creation_timestamp == current_time → age 0 → decay_factor = 1.0."""
    svc = _make_service()
    now_sec = 1_700_000_000.0
    now_ms = int(now_sec * 1000)
    result = svc.compute_decay_factor(now_ms, current_time=now_sec)
    assert result == 1.0


def test_zero_timestamp_is_very_old():
    """Timestamp 0 (epoch) → very old → decay ≈ 0.0."""
    svc = _make_service()
    now_sec = 1_700_000_000.0
    result = svc.compute_decay_factor(0, current_time=now_sec)
    # e^(-1.7e9 / 3600 / (365*24)) ≈ e^(-53.9) ≈ 3.88e-24
    assert result < 1e-20
