"""
retry.py — Async exponential backoff retry decorator for AI provider calls.

Retryable errors: network failures, rate limits (429), server errors (5xx).
Non-retryable errors: bad requests (400), auth (401/403), not found (404).
"""

import asyncio
import logging
from typing import Callable, Awaitable, TypeVar, Any

T = TypeVar("T")

# HTTP status codes that are worth retrying.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# HTTP status codes that should fail immediately — no point retrying.
_FATAL_STATUS = {400, 401, 403, 404, 422}

# Exception types that indicate transient network problems.
_RETRYABLE_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    OSError,
)


def _is_retryable(exc: Exception) -> bool:
    """
    Return True if the exception represents a transient failure worth retrying.

    Checks:
    - Exception type (network/timeout errors)
    - HTTP status code embedded in the exception message or attributes
    """
    # Check for explicit status_code attribute (httpx, aiohttp, openai SDK).
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status is not None:
        if status in _FATAL_STATUS:
            return False
        if status in _RETRYABLE_STATUS:
            return True

    # Check for status code in the exception message string.
    msg = str(exc)
    for code in _RETRYABLE_STATUS:
        if str(code) in msg:
            return True
    for code in _FATAL_STATUS:
        if str(code) in msg:
            return False

    # Fall back to exception type check.
    return isinstance(exc, _RETRYABLE_EXCEPTIONS)


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    max_attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff: float = 2.0,
    label: str = "call",
) -> T:
    """
    Execute an async callable with exponential backoff retry.

    Args:
        fn:           Async callable to execute (no arguments — use a lambda/partial).
        max_attempts: Maximum number of total attempts (default 5).
        base_delay:   Initial retry delay in seconds (default 1.0).
        max_delay:    Maximum delay cap in seconds (default 30.0).
        backoff:      Exponential multiplier applied each retry (default 2.0).
        label:        Human-readable label for log messages.

    Returns:
        Return value of ``fn`` on success.

    Raises:
        The last exception if all attempts fail.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")

    delay = base_delay
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc

            if not _is_retryable(exc):
                logging.debug(f"[retry] {label} — non-retryable error: {exc}")
                raise

            if attempt >= max_attempts:
                logging.warning(
                    f"[retry] {label} — failed after {max_attempts} attempt(s): {exc}"
                )
                raise

            logging.warning(
                f"[retry] {label} — attempt {attempt}/{max_attempts} failed: {exc}. "
                f"Retrying in {delay:.1f}s..."
            )
            await asyncio.sleep(delay)
            delay = min(delay * backoff, max_delay)

    raise last_exc  # unreachable but satisfies type checker
