"""
Tests for _is_retryable and with_retry in lib/utils/retry.py
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.utils.retry import _is_retryable, with_retry

# --- helpers ---

def _exc(msg="", **attrs):
    """Create an Exception with optional message and attributes."""
    exc = Exception(msg)
    for k, v in attrs.items():
        setattr(exc, k, v)
    return exc


# --- retryable status_code attribute ---

def test_status_code_429():
    assert _is_retryable(_exc(status_code=429))


def test_status_code_500():
    assert _is_retryable(_exc(status_code=500))


def test_status_code_502():
    assert _is_retryable(_exc(status_code=502))


def test_status_code_503():
    assert _is_retryable(_exc(status_code=503))


def test_status_code_504():
    assert _is_retryable(_exc(status_code=504))


# --- fatal status_code attribute ---

def test_status_code_400():
    assert not _is_retryable(_exc(status_code=400))


def test_status_code_401():
    assert not _is_retryable(_exc(status_code=401))


def test_status_code_403():
    assert not _is_retryable(_exc(status_code=403))


def test_status_code_404():
    assert not _is_retryable(_exc(status_code=404))


def test_status_code_422():
    assert not _is_retryable(_exc(status_code=422))


# --- retryable status code in message string ---

def test_message_contains_429():
    assert _is_retryable(_exc("Error 429: Too Many Requests"))


def test_message_contains_503():
    assert _is_retryable(_exc("Service Unavailable (503)"))


def test_message_contains_504():
    assert _is_retryable(_exc("504 Gateway Timeout"))


# --- fatal status code in message string ---

def test_message_contains_400():
    assert not _is_retryable(_exc("Bad Request 400"))


def test_message_contains_401():
    assert not _is_retryable(_exc("401 Unauthorized"))


def test_message_contains_403():
    assert not _is_retryable(_exc("Forbidden (403)"))


def test_message_contains_404():
    assert not _is_retryable(_exc("Not Found: 404"))


def test_message_contains_422():
    assert not _is_retryable(_exc("Unprocessable 422"))


# --- retryable exception types ---

def test_connection_error():
    try:
        raise ConnectionError("connection refused")
    except ConnectionError as e:
        assert _is_retryable(e)


def test_timeout_error():
    try:
        raise TimeoutError("timed out")
    except TimeoutError as e:
        assert _is_retryable(e)


def test_os_error():
    try:
        raise OSError("network unreachable")
    except OSError as e:
        assert _is_retryable(e)


# --- non-retryable exception types ---

def test_value_error():
    try:
        raise ValueError("bad value")
    except ValueError as e:
        assert not _is_retryable(e)


def test_type_error():
    try:
        raise TypeError("bad type")
    except TypeError as e:
        assert not _is_retryable(e)


def test_key_error():
    try:
        raise KeyError("missing key")
    except KeyError as e:
        assert not _is_retryable(e)


# --- status attribute instead of status_code ---

def test_status_attr_retryable():
    assert _is_retryable(_exc(status=429))


def test_status_attr_fatal():
    assert not _is_retryable(_exc(status=400))


# --- priority: status_code takes precedence over message ---

def test_status_code_wins_over_message_retryable():
    """status_code=503 (retryable) should return True even if message has 400."""
    assert _is_retryable(_exc("got 400", status_code=503))


def test_status_code_wins_over_message_fatal():
    """status_code=400 (fatal) should return False even if message has 503."""
    assert not _is_retryable(_exc("got 503", status_code=400))


# --- priority: status_code takes precedence over status ---

def test_status_code_before_status():
    """status_code=400 should take priority over status=503."""
    assert not _is_retryable(_exc(status_code=400, status=503))


# --- edge cases ---

def test_unknown_status_code_falls_through():
    """status_code=418 (neither retryable nor fatal) should fall to message/type."""
    assert not _is_retryable(_exc(status_code=418))


def test_unknown_status_falls_through():
    """status=418 (neither retryable nor fatal) should fall to message/type."""
    assert not _is_retryable(_exc(status=418))


def test_both_codes_in_message_retryable_first():
    """Retryable codes are checked before fatal; 503 in message → True."""
    assert _is_retryable(_exc("503 then 400"))


def test_fatal_only_in_message():
    assert not _is_retryable(_exc("got 404"))


def test_empty_message():
    assert not _is_retryable(_exc(""))


def test_plain_exception():
    assert not _is_retryable(Exception("something broke"))


# ============================================================
# with_retry tests
# ============================================================


@pytest.mark.asyncio
async def test_with_retry_succeeds_first_attempt():
    """Function succeeds on first attempt → returns result immediately, no sleep."""
    mock_fn = AsyncMock(return_value="result")

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await with_retry(mock_fn)

    assert result == "result"
    mock_fn.assert_called_once()
    mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_with_retry_retries_and_succeeds():
    """Fails with retryable error then succeeds on 2nd attempt."""
    mock_fn = AsyncMock(side_effect=[ConnectionError("refused"), "result"])

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await with_retry(mock_fn, base_delay=0.1)

    assert result == "result"
    assert mock_fn.call_count == 2
    mock_sleep.assert_called_once_with(0.1)


@pytest.mark.asyncio
async def test_with_retry_non_retryable_raises_immediately():
    """Non-retryable error raises immediately without any retry."""
    mock_fn = AsyncMock(side_effect=ValueError("bad value"))

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        try:
            await with_retry(mock_fn)
            assert False, "should have raised ValueError"
        except ValueError:
            pass

    mock_fn.assert_called_once()
    mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_with_retry_exhausts_attempts():
    """Function fails on every attempt → raises last exception after all retries."""
    mock_fn = AsyncMock(side_effect=ConnectionError("refused"))

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        try:
            await with_retry(mock_fn, max_attempts=3, base_delay=0.1)
            assert False, "should have raised ConnectionError"
        except ConnectionError:
            pass

    assert mock_fn.call_count == 3
    assert mock_sleep.call_count == 2  # sleeps before attempt 2 and 3


@pytest.mark.asyncio
async def test_with_retry_max_attempts_less_than_one():
    """max_attempts < 1 raises ValueError."""
    try:
        await with_retry(AsyncMock(), max_attempts=0)
        assert False, "should have raised ValueError"
    except ValueError as e:
        assert "max_attempts" in str(e)


@pytest.mark.asyncio
async def test_with_retry_exponential_backoff():
    """Delay doubles between attempts."""
    mock_fn = AsyncMock(side_effect=[ConnectionError("e")] * 4 + ["result"])

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await with_retry(
            mock_fn, max_attempts=5, base_delay=1.0, backoff=2.0
        )

    assert result == "result"
    delays = [call.args[0] for call in mock_sleep.call_args_list]
    assert delays == [1.0, 2.0, 4.0, 8.0]


@pytest.mark.asyncio
async def test_with_retry_backoff_capped_at_max_delay():
    """Delay capped at max_delay even with exponential growth."""
    mock_fn = AsyncMock(side_effect=[ConnectionError("e")] * 5 + ["result"])

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await with_retry(
            mock_fn, max_attempts=6, base_delay=10.0, backoff=2.0, max_delay=30.0
        )

    assert result == "result"
    delays = [call.args[0] for call in mock_sleep.call_args_list]
    assert delays == [10.0, 20.0, 30.0, 30.0, 30.0]


@pytest.mark.asyncio
async def test_with_retry_default_parameters():
    """Default params: 5 attempts, delays [1.0, 2.0, 4.0, 8.0]."""
    mock_fn = AsyncMock(side_effect=[ConnectionError("e")] * 4 + ["result"])

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await with_retry(mock_fn)

    assert result == "result"
    assert mock_fn.call_count == 5
    delays = [call.args[0] for call in mock_sleep.call_args_list]
    assert delays == [1.0, 2.0, 4.0, 8.0]


# --- runner ---

if __name__ == "__main__":
    import pytest
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
