"""
Tests for lib/global_registry.py

Covers:
- get_or_create() — creates on first call, returns cached on second
- get_or_create() with factory that raises — logs and re-raises
- get() — returns None for unknown key
- exists() — returns True/False
- clear_key() — removes key, calls close() if available
- clear_key() with close() that raises — logs warning, not propagated
- clear_all() — iterates all keys calling clear_key
- clear_all() on empty registry — no-op
- Singleton pattern: second GlobalRegistry() returns same instance
"""

import io
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.global_registry import GlobalRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class HasClose:
    """Object with a well-behaved close() method."""
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class HasCloseThatRaises:
    """Object whose close() method raises."""
    def close(self):
        raise RuntimeError("close failed")


def _create_fresh_registry():
    """Return a fresh GlobalRegistry by resetting the singleton."""
    GlobalRegistry._instance = None
    return GlobalRegistry()


def _capture_logs(level=logging.WARNING):
    """Capture log output at the given level or above."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    logger = logging.getLogger()
    logger.addHandler(handler)
    return stream, handler


def _release_logs(handler):
    """Remove a log capture handler from the root logger."""
    logging.getLogger().removeHandler(handler)


# ---------------------------------------------------------------------------
# get_or_create
# ---------------------------------------------------------------------------

def test_get_or_create_creates_on_first_call():
    """get_or_create calls factory and stores result on first call."""
    reg = _create_fresh_registry()
    obj = object()
    result = reg.get_or_create("test.obj", lambda: obj)
    assert result is obj, f"returned {result!r}, expected {obj!r}"
    assert reg.exists("test.obj"), "key should exist"


def test_get_or_create_returns_cached_on_second_call():
    """get_or_create returns the same instance on subsequent calls."""
    reg = _create_fresh_registry()
    obj = reg.get_or_create("key1", lambda: object())
    obj2 = reg.get_or_create("key1", lambda: object())  # different factory
    assert obj is obj2, "second call returned different instance"


def test_get_or_create_passes_args_to_factory():
    """Factory receives positional and keyword arguments."""
    reg = _create_fresh_registry()

    def factory(a, b=0):
        return a + b

    result = reg.get_or_create("sum", factory, 3, b=7)
    assert result == 10, f"expected 10, got {result}"


def test_get_or_create_factory_raises_logs_and_reraises():
    """If the factory raises, it should be logged and re-raised."""
    reg = _create_fresh_registry()
    stream, handler = _capture_logs(level=logging.ERROR)

    try:
        reg.get_or_create("bad", lambda: (_ for _ in ()).throw(ValueError("boom")))
        pytest.fail("expected exception not raised")
    except ValueError as e:
        assert "boom" in str(e), f"wrong exception message: {e}"
    finally:
        _release_logs(handler)

    log_output = stream.getvalue()
    assert "boom" in log_output, f"expected 'boom' in logs, got: {log_output!r}"
    assert "Failed to create instance" in log_output, (
        "expected 'Failed to create instance' in logs"
    )

    # Key should NOT exist after failed creation
    assert not reg.exists("bad"), "key should not exist after failed creation"


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------

def test_get_returns_instance_for_known_key():
    """get returns the instance for a known key."""
    reg = _create_fresh_registry()
    obj = reg.get_or_create("known", lambda: {"x": 1})
    result = reg.get("known")
    assert result is obj, f"returned {result!r}, expected {obj!r}"


def test_get_returns_none_for_unknown_key():
    """get returns None when the key is unknown."""
    reg = _create_fresh_registry()
    result = reg.get("no.such.key")
    assert result is None, f"expected None, got {result!r}"


# ---------------------------------------------------------------------------
# exists
# ---------------------------------------------------------------------------

def test_exists_returns_true_for_existing_key():
    """exists returns True when the key is in the registry."""
    reg = _create_fresh_registry()
    reg.get_or_create("here", lambda: 42)
    assert reg.exists("here"), "key should exist"


def test_exists_returns_false_for_missing_key():
    """exists returns False when the key is not in the registry."""
    reg = _create_fresh_registry()
    assert not reg.exists("nowhere"), "key should not exist"


# ---------------------------------------------------------------------------
# clear_key
# ---------------------------------------------------------------------------

def test_clear_key_removes_key():
    """clear_key removes a key from the registry."""
    reg = _create_fresh_registry()
    reg.get_or_create("temp", lambda: object())
    reg.clear_key("temp")
    assert not reg.exists("temp"), "key should be removed"
    assert reg.get("temp") is None, "get should return None"


def test_clear_key_calls_close_if_available():
    """clear_key calls close() on the instance if it exists."""
    reg = _create_fresh_registry()
    closable = HasClose()
    reg.get_or_create("clo", lambda: closable)
    reg.clear_key("clo")
    assert closable.closed, "close() was not called"
    assert not reg.exists("clo"), "key should be removed"


def test_clear_key_close_raises_logs_warning_does_not_propagate():
    """clear_key should log warning if close() raises, not propagate the exception."""
    reg = _create_fresh_registry()
    obj = HasCloseThatRaises()
    reg.get_or_create("badclose", lambda: obj)
    stream, handler = _capture_logs(level=logging.WARNING)

    reg.clear_key("badclose")  # must not raise

    _release_logs(handler)
    log_output = stream.getvalue()
    assert "close failed" in log_output, (
        f"expected 'close failed' in logs, got: {log_output!r}"
    )
    assert not reg.exists("badclose"), (
        "key should be removed even when close() fails"
    )


def test_clear_key_unknown_key_is_noop():
    """clear_key on an unknown key does nothing and does not raise."""
    reg = _create_fresh_registry()
    reg.clear_key("not.here")  # should not raise


# ---------------------------------------------------------------------------
# clear_all
# ---------------------------------------------------------------------------

def test_clear_all_removes_all_keys():
    """clear_all removes all keys from the registry."""
    reg = _create_fresh_registry()
    reg.get_or_create("a", lambda: object())
    reg.get_or_create("b", lambda: object())
    reg.get_or_create("c", lambda: object())
    reg.clear_all()
    assert reg.get("a") is None and reg.get("b") is None and reg.get("c") is None, (
        "keys not removed"
    )


def test_clear_all_calls_close_on_all_instances():
    """clear_all calls close() on every instance that has it."""
    reg = _create_fresh_registry()
    closable_a = HasClose()
    closable_b = HasClose()
    reg.get_or_create("x", lambda: closable_a)
    reg.get_or_create("y", lambda: closable_b)
    reg.clear_all()
    assert closable_a.closed, "close() not called on a"
    assert closable_b.closed, "close() not called on b"


def test_clear_all_on_empty_registry_is_noop():
    """clear_all on an empty registry is a no-op."""
    reg = _create_fresh_registry()
    reg.clear_all()  # should not raise


def test_clear_all_with_one_failing_close_still_clears_others():
    """clear_all continues cleaning other keys when one close() raises."""
    reg = _create_fresh_registry()
    bad = HasCloseThatRaises()
    good = HasClose()
    reg.get_or_create("bad", lambda: bad)
    reg.get_or_create("good", lambda: good)
    reg.clear_all()  # must not raise
    assert good.closed, "close() not called on good"
    assert not (reg.exists("bad") or reg.exists("good")), (
        "keys should all be removed"
    )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

def test_singleton_second_instance_is_same():
    """A second GlobalRegistry() returns the exact same instance."""
    GlobalRegistry._instance = None  # reset
    reg1 = GlobalRegistry()
    reg2 = GlobalRegistry()
    assert reg1 is reg2, "instances differ"


def test_singleton_shares_registry_data():
    """Two references to the singleton share the same registry data."""
    GlobalRegistry._instance = None
    reg1 = GlobalRegistry()
    reg2 = GlobalRegistry()
    obj = object()
    reg1.get_or_create("shared", lambda: obj)
    assert reg2.get("shared") is obj, "reg2 did not see reg1's data"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
