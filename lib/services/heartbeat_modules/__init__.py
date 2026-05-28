"""Heartbeat modules for HeartbeatService."""

from lib.services.heartbeat_modules.system_health import SystemHealthCheck
from lib.services.heartbeat_modules.memory_grooming import MemoryGrooming
from lib.services.heartbeat_modules.dream import DreamModule
from lib.services.heartbeat_modules.curiosity import CuriosityModule
from lib.services.heartbeat_modules.session_cache_cleanup import SessionCacheCleanup

__all__ = [
    "SystemHealthCheck",
    "MemoryGrooming",
    "DreamModule",
    "CuriosityModule",
    "SessionCacheCleanup",
]
