"""
Heartbeat modules for custom Bio+Mood system.

BioTickModule: Ticks the BiologyEngine every 5 minutes.
MoodTickModule: Ticks the MoodEngine every 15 minutes.
"""
from __future__ import annotations

import logging
from lib.services.heartbeat_module import HeartbeatModule

log = logging.getLogger("nami.custom")


class BioTickModule(HeartbeatModule):
    """Periodic biology tick — advances decay, regeneration, and circadian."""

    name = "bio_tick"
    priority = 60
    cooldown_seconds = 300  # 5 minutes

    def __init__(self, bio_engine, config: dict | None = None):
        super().__init__()
        self.bio_engine = bio_engine
        self._cfg = config or {}

    async def condition(self) -> bool:
        return self.bio_engine is not None

    async def action(self) -> None:
        try:
            await self.bio_engine.tick()
        except Exception as e:
            log.error("BioTick failed: %s", e)


class MoodTickModule(HeartbeatModule):
    """Periodic mood evaluation — 20% chance of mood change every 15 minutes."""

    name = "mood_tick"
    priority = 55
    cooldown_seconds = 900  # 15 minutes

    def __init__(self, mood_engine, config: dict | None = None):
        super().__init__()
        self.mood_engine = mood_engine
        self._cfg = config or {}

    async def condition(self) -> bool:
        return self.mood_engine is not None

    async def action(self) -> None:
        try:
            new_mood = await self.mood_engine.tick()
            if new_mood:
                log.info("MoodTick: mood changed to '%s'", new_mood)
        except Exception as e:
            log.error("MoodTick failed: %s", e)
