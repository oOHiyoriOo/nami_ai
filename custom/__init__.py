"""
Custom Bio+Mood Module — pluggable autonomous agent simulation for Nami AI.

Usage (in app_initializer.py, after curiosity module registration):
    from custom import register_all
    register_all(heartbeat, g_data, config)

This registers:
- BiologyEngine + NeedsEngine + ConsequenceTracker (bio sim)
- MoodEngine + PADIdentityDrift (personality)
- BioTickModule + MoodTickModule (heartbeat ticks)
- identity.on_interaction() hooked to activity.recorded
- {{bio_state}} and {{mood}} template variables for system prompts
"""
from __future__ import annotations

import logging

from custom.biology.engine import BiologyEngine
from custom.biology.needs import NeedsEngine
from custom.biology.consequences import ConsequenceTracker
from custom.mood.engine import MoodEngine
from custom.mood.identity import PADIdentityDrift
from custom.heartbeat_modules import BioTickModule, MoodTickModule

log = logging.getLogger("nami.custom")


def register_all(heartbeat, g_data, config) -> None:
    """
    Register all custom bio+mood services into Nami's global registry
    and heartbeat loop.

    Args:
        heartbeat: HeartbeatService instance (for registering tick modules)
        g_data: GlobalRegistry singleton
        config: ConfigurationFile instance (for reading bio/mood config sections)
    """
    bio_cfg = config.data.get("biology", {})
    mood_cfg = config.data.get("mood", {})
    paths = config.data.get("paths", {})
    db_path = paths.get("scheduler_db", "scheduler.db")

    # ── Biology Engine ──────────────────────────────────────────────
    log.info("[custom] Initializing BiologyEngine...")
    bio_engine = BiologyEngine(db_path=db_path, config=bio_cfg)
    consequence_tracker = ConsequenceTracker()
    needs_engine = NeedsEngine()

    # Wire consequence tracker into bio engine
    bio_engine.set_consequence_tracker(consequence_tracker)

    # Load persisted state
    import asyncio
    asyncio.ensure_future(bio_engine.load_state())

    # Register heartbeat module
    if bio_cfg.get("enabled", True):
        bio_tick = BioTickModule(bio_engine, config=bio_cfg)
        heartbeat.register(bio_tick)
        log.info("[custom] BioTickModule registered (priority=%d, cooldown=%ds)",
                 bio_tick.priority, bio_tick.cooldown_seconds)

    # ── Mood Engine ─────────────────────────────────────────────────
    log.info("[custom] Initializing MoodEngine...")
    mood_engine = MoodEngine(db_path=db_path)
    mood_engine.set_bio_engine(bio_engine)

    # Load persisted state
    asyncio.ensure_future(mood_engine.load_state())

    # ── PAD Identity Drift ──────────────────────────────────────────
    log.info("[custom] Initializing PADIdentityDrift...")
    identity = PADIdentityDrift()

    # Register heartbeat module
    if mood_cfg.get("enabled", True):
        mood_tick = MoodTickModule(mood_engine, config=mood_cfg)
        heartbeat.register(mood_tick)
        log.info("[custom] MoodTickModule registered (priority=%d, cooldown=%ds)",
                 mood_tick.priority, mood_tick.cooldown_seconds)

    # ── Register in global registry ─────────────────────────────────
    g_data.get_or_create("bio_engine", lambda: bio_engine)
    g_data.get_or_create("needs_engine", lambda: needs_engine)
    g_data.get_or_create("consequence_tracker", lambda: consequence_tracker)
    g_data.get_or_create("mood_engine", lambda: mood_engine)
    g_data.get_or_create("identity_drift", lambda: identity)

    # ── Hook identity drift to activity ─────────────────────────────
    event_bus = g_data.get("event_bus")
    if event_bus:
        async def _on_activity(event):
            """Shift PAD identity on every user interaction."""
            try:
                identity.on_interaction(was_warm=True)
            except Exception as e:
                log.debug("Identity drift hook error: %s", e)

        event_bus.subscribe("activity.recorded", _on_activity)
        log.info("[custom] Identity drift hooked to activity.recorded")

    # ── Inject template variables into system prompt ────────────────
    sys_prompt = g_data.get("system_prompt")
    if sys_prompt:

        async def _bio_state_hint():
            """Resolve {{bio_state}} template variable."""
            try:
                hint = bio_engine.get_context_hint()
                needs_hint = needs_engine.to_prompt_hint(bio_engine.state)
                urgency = bio_engine.urgency_hint()
                parts = [f"[SYSTEM STATE — Biology] {hint}"]
                if needs_hint:
                    parts.append(needs_hint)
                if urgency:
                    parts.append(f"[URGENCY] {urgency}")
                return "\n".join(parts)
            except Exception as e:
                log.debug("bio_state hint error: %s", e)
                return ""

        async def _mood_hint():
            """Resolve {{mood}} template variable."""
            try:
                mood = mood_engine.get_llm_hint()
                id_hint = identity.get_llm_hint()
                parts = []
                if mood:
                    parts.append(mood)
                if id_hint:
                    parts.append(id_hint)
                return "\n".join(parts) if parts else ""
            except Exception as e:
                log.debug("mood hint error: %s", e)
                return ""

        # Monkey-patch methods onto the NamiSystemPrompt instance
        sys_prompt.bio_state = _bio_state_hint
        sys_prompt.mood = _mood_hint
        log.info("[custom] Template variables {{bio_state}} and {{mood}} registered")

    log.info("[custom] Bio+Mood module fully registered")
