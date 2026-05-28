"""
session_cache_cleanup.py — HeartbeatModule: prune old session caches.

Runs periodically (default: once per hour) to delete .nami_session_cache/
entries older than a configurable threshold (default: 7 days).
"""

import logging

from lib.services.heartbeat_module import HeartbeatModule
from lib.services.nami_session_cache import cleanup_old_sessions, _resolve_project_root


class SessionCacheCleanup(HeartbeatModule):
    """Prune session caches older than max_age_days."""

    name = "session_cache_cleanup"
    priority = 20
    cooldown_seconds = 3600  # Once per hour

    def __init__(self, max_age_days: int = 7) -> None:
        super().__init__()
        self.max_age_days = max_age_days

    async def condition(self) -> bool:
        return True  # Always eligible; cooldown handles rate-limiting

    async def action(self) -> None:
        project_root = _resolve_project_root()
        deleted = cleanup_old_sessions(project_root, self.max_age_days)
        if deleted:
            logging.info(
                f"[heartbeat.session_cache_cleanup] Pruned {deleted} old session(s) "
                f"(older than {self.max_age_days} days)"
            )
