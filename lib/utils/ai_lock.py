"""
Activity-based AI lock acquisition.

Instead of a hard deadline, callers waiting for ``g_data["ai_lock"]`` retry
every ``check_interval`` seconds and watch ``_last_activity``.  Whenever the
lock holder calls :func:`bump_activity` (e.g. before each tool call) the
inactivity window resets.  Only if the holder stops reporting progress for
``inactivity_timeout`` consecutive seconds do we give up and drop the pending
work.

Usage::

    from lib.utils.ai_lock import acquire_ai_lock, bump_activity

    lock = g_data.get("ai_lock")
    if not await acquire_ai_lock(lock, label="chat"):
        return  # stuck — drop this request
    try:
        bump_activity()        # holder starts work
        ...
    finally:
        lock.release()
"""

import asyncio
import logging
import time

# Module-level monotonic clock stamp.  All code that needs to signal
# "something is happening" calls bump_activity(); all code waiting to acquire
# the lock reads _last_activity directly via acquire_ai_lock().
_last_activity: float = 0.0


def bump_activity() -> None:
    """Record the current moment as the latest AI-work heartbeat.

    Call this whenever the lock holder makes meaningful forward progress —
    lock acquisition, tool call dispatch, etc.  Waiters inside
    :func:`acquire_ai_lock` use this signal to extend their patience.
    """
    global _last_activity
    _last_activity = time.monotonic()


async def acquire_ai_lock(
    lock: asyncio.Lock,
    *,
    inactivity_timeout: float = 1800.0,
    check_interval: float = 30.0,
    label: str = "caller",
) -> bool:
    """Acquire *lock* with an inactivity-based (not wall-clock) timeout.

    Polls every *check_interval* seconds.  After each failed attempt the
    waiter checks whether the holder has called :func:`bump_activity` since
    the previous check:

    * **Activity seen** → the holder is alive; reset the inactivity counter
      and keep waiting indefinitely.
    * **No activity** for *inactivity_timeout* seconds → assume the holder is
      stuck; log an error and return ``False`` so the caller can drop its work
      gracefully.

    On success, :func:`bump_activity` is called automatically so the new
    holder's work counts as activity immediately.

    Args:
        lock:               The ``asyncio.Lock`` to acquire.
        inactivity_timeout: Give up when the holder has been silent for this
                            many seconds (default: 1800 s / 30 min).
        check_interval:     Polling interval in seconds (default: 30 s).
        label:              Human-readable tag used in log messages.

    Returns:
        ``True`` if the lock was acquired; ``False`` if timed out.
    """
    last_seen_activity: float = _last_activity

    while True:
        try:
            await asyncio.wait_for(lock.acquire(), timeout=check_interval)
            bump_activity()  # new holder — register start of work
            return True
        except asyncio.TimeoutError:
            if _last_activity != last_seen_activity:
                # Holder made progress since our last check — keep waiting
                last_seen_activity = _last_activity
                logging.debug(
                    "[ai_lock] %s still queued — holder is active, retrying",
                    label,
                )
                continue

            idle = time.monotonic() - _last_activity
            if idle >= inactivity_timeout:
                logging.error(
                    "[ai_lock] %s giving up — lock holder silent for %.0fs "
                    "(threshold=%.0fs)",
                    label,
                    idle,
                    inactivity_timeout,
                )
                return False

            logging.debug(
                "[ai_lock] %s still queued — holder idle %.0fs / %.0fs",
                label,
                idle,
                inactivity_timeout,
            )
