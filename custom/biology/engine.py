"""
BiologyEngine — autonomous bio-state simulation.

Simulates four vital stats (energy, hunger, social, cognitive) that decay over
time and regenerate during sleep. Stats are persisted to SQLite (scheduler.db)
so they survive server restarts.

Architecture:
- tick(dt_seconds) — called periodically by BioTick heartbeat module
- apply_action(name) — called when AI performs an action (eat, sleep, etc.)
- get_context_hint() — returns a human-readable label for LLM context injection
- get_state() / load_state() — SQLite persistence

Formula corrections vs. sister experimental code:
- Modifiers applied AFTER tick multiplication (fixed double-scaling)
- Unified English action names (no German/English mismatch)
- Consequence modifiers recalculated each tick
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

log = logging.getLogger("nami.biology")


# ── Configuration defaults ──────────────────────────────────────────

DEFAULT_DECAY_PER_5MIN = {
    "energy":    -1.5,
    "hunger":    +2.0,
    "social":    -0.8,
    "cognitive": -1.0,
}

DEFAULT_SLEEP_REGEN_PER_5MIN = {
    "energy":    +6.0,
    "hunger":    +1.2,
    "social":    +2.0,
    "cognitive": -3.0,
}

# Action effects: {action_name: {stat: delta}}
# All names in English (unified — no German mismatch)
ACTION_EFFECTS = {
    "sleep":     {"energy": +5, "hunger": +2, "social": +3, "cognitive": -5},
    "eat":       {"energy": +8, "hunger": -35, "social": 0, "cognitive": +2},
    "snack":     {"energy": +3, "hunger": -15, "social": 0, "cognitive": 0},
    "study":     {"energy": -5, "hunger": +3, "social": -3, "cognitive": +12},
    "read":      {"energy": -3, "hunger": +1, "social": 0, "cognitive": +5},
    "browse":    {"energy": -2, "hunger": +1, "social": +2, "cognitive": +3},
    "talk":      {"energy": -4, "hunger": +2, "social": +15, "cognitive": +5},
    "rest":      {"energy": +10, "hunger": +2, "social": +2, "cognitive": -8},
    "water":     {"energy": +2, "hunger": -5, "social": +5, "cognitive": +1},
    "exercise":  {"energy": -15, "hunger": +20, "social": +5, "cognitive": -10},
}

# Circadian modifiers by hour → {stat: multiplier}
# 0 = midnight, 6 = dawn, 12 = noon, 18 = dusk
CIRCADIAN_MODIFIERS = {
    # Late night (0-5): low energy, moderate hunger
    0:  {"energy": 1.3, "hunger": 0.7, "cognitive": 1.2},
    1:  {"energy": 1.4, "hunger": 0.6, "cognitive": 1.3},
    2:  {"energy": 1.5, "hunger": 0.5, "cognitive": 1.4},
    3:  {"energy": 1.5, "hunger": 0.5, "cognitive": 1.5},
    4:  {"energy": 1.4, "hunger": 0.6, "cognitive": 1.3},
    5:  {"energy": 1.2, "hunger": 0.8, "cognitive": 1.1},
    # Morning (6-11): energy boost, hunger rising
    6:  {"energy": 0.8, "hunger": 1.2, "cognitive": 0.9},
    7:  {"energy": 0.6, "hunger": 1.4, "cognitive": 0.8},
    8:  {"energy": 0.5, "hunger": 1.5, "cognitive": 0.7},
    9:  {"energy": 0.5, "hunger": 1.4, "cognitive": 0.7},
    10: {"energy": 0.6, "hunger": 1.3, "cognitive": 0.8},
    11: {"energy": 0.7, "hunger": 1.2, "cognitive": 0.9},
    # Midday (12-17): stable
    12: {"energy": 0.9, "hunger": 1.0, "cognitive": 1.0},
    13: {"energy": 1.0, "hunger": 1.0, "cognitive": 1.0},
    14: {"energy": 1.1, "hunger": 1.0, "cognitive": 1.1},
    15: {"energy": 1.1, "hunger": 1.1, "cognitive": 1.1},
    16: {"energy": 1.0, "hunger": 1.2, "cognitive": 1.0},
    17: {"energy": 0.9, "hunger": 1.3, "cognitive": 0.9},
    # Evening (18-23): energy fading
    18: {"energy": 1.0, "hunger": 1.2, "cognitive": 0.9},
    19: {"energy": 1.1, "hunger": 1.1, "cognitive": 0.9},
    20: {"energy": 1.2, "hunger": 1.0, "cognitive": 1.0},
    21: {"energy": 1.3, "hunger": 0.9, "cognitive": 1.1},
    22: {"energy": 1.4, "hunger": 0.8, "cognitive": 1.2},
    23: {"energy": 1.4, "hunger": 0.7, "cognitive": 1.3},
}


# ── State dataclass ─────────────────────────────────────────────────

@dataclass
class BiologyState:
    """Snapshot of all bio stats. All values clamped to [0, 100]."""
    energy: float = 75.0
    hunger: float = 20.0
    social: float = 55.0
    cognitive: float = 30.0
    is_sleeping: bool = False
    last_tick: float = 0.0
    last_ate: Optional[str] = None
    awake_since: Optional[str] = None
    last_meal_timestamp: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "energy": self.energy, "hunger": self.hunger,
            "social": self.social, "cognitive": self.cognitive,
            "is_sleeping": self.is_sleeping, "last_tick": self.last_tick,
            "last_ate": self.last_ate, "awake_since": self.awake_since,
            "last_meal_timestamp": self.last_meal_timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BiologyState":
        return cls(
            energy=float(d.get("energy", 75.0)),
            hunger=float(d.get("hunger", 20.0)),
            social=float(d.get("social", 55.0)),
            cognitive=float(d.get("cognitive", 30.0)),
            is_sleeping=bool(d.get("is_sleeping", False)),
            last_tick=float(d.get("last_tick", 0.0)),
            last_ate=d.get("last_ate"),
            awake_since=d.get("awake_since"),
            last_meal_timestamp=float(d["last_meal_timestamp"]) if d.get("last_meal_timestamp") else None,
        )


# ── Label helpers ───────────────────────────────────────────────────

def _label(value: float, bands: list[tuple[str, float]]) -> str:
    """Return first label where value >= threshold (bands sorted high→low)."""
    for name, threshold in sorted(bands, key=lambda x: x[1], reverse=True):
        if value >= threshold:
            return name
    return bands[-1][0] if bands else "?"


ENERGY_BANDS = [
    ("voller Energie", 80), ("gut drauf", 55), ("etwas müde", 35),
    ("erschöpft", 15), ("kurz vor dem Einschlafen", 0),
]
HUNGER_BANDS = [
    ("am Verhungern", 80), ("ziemlich hungrig", 55), ("ein bisschen hungrig", 30),
    ("satt", 10), ("übersatt", 0),
]
SOCIAL_BANDS = [
    ("gesellig & gesprächig", 75), ("normal", 45), ("braucht Abstand", 20),
    ("reizbar & introvertiert", 5), ("will NIEMANDEN sehen", 0),
]
COGNITIVE_BANDS = [
    ("überstimuliert — Kopf raucht", 85), ("intensiv am Denken", 60),
    ("konzentriert", 35), ("entspannt", 10), ("völlig leer im Kopf", 0),
]


# ── BiologyEngine ───────────────────────────────────────────────────

class BiologyEngine:
    """Autonomous bio-state simulation with decay, sleep, eating, and consequences."""

    TICK_INTERVAL = 300  # 5 minutes in seconds

    def __init__(self, db_path: str = "scheduler.db", config: dict | None = None):
        self.db_path = db_path
        self.cfg = config or {}
        self._state = BiologyState()
        self._consequence_tracker = None  # set later via set_consequence_tracker()

    # ── Persistence ─────────────────────────────────────────────────

    async def _ensure_table(self):
        """Create bio_state table in scheduler.db if not exists."""
        def _do():
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS bio_state (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    )
                """)
                conn.commit()
        await asyncio.to_thread(_do)

    async def load_state(self) -> BiologyState:
        """Load persisted bio state from SQLite."""
        await self._ensure_table()

        def _do():
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT value FROM bio_state WHERE key = 'biology'"
                ).fetchone()
                if row:
                    return BiologyState.from_dict(json.loads(row[0]))
                return BiologyState(last_tick=time.time())
        self._state = await asyncio.to_thread(_do)
        if self._state.last_tick == 0.0:
            self._state.last_tick = time.time()
        log.debug("Bio state loaded: e=%.0f h=%.0f s=%.0f c=%.0f sleep=%s",
                   self._state.energy, self._state.hunger,
                   self._state.social, self._state.cognitive,
                   self._state.is_sleeping)
        return self._state

    async def save_state(self):
        """Persist bio state to SQLite."""
        await self._ensure_table()

        def _do():
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO bio_state (key, value, updated_at) VALUES (?, ?, ?)",
                    ("biology", json.dumps(self._state.to_dict()), time.time()),
                )
                conn.commit()
        await asyncio.to_thread(_do)

    # ── Consequence tracker ─────────────────────────────────────────

    def set_consequence_tracker(self, tracker):
        """Wire in the ConsequenceTracker for modifier access."""
        self._consequence_tracker = tracker

    def _get_consequence_modifiers(self) -> dict:
        """Get current consequence modifiers, or zeros if tracker not set."""
        if self._consequence_tracker:
            return self._consequence_tracker.get_modifiers()
        return {
            "cognitive_decay_bonus": 0.0,
            "sleep_quality_penalty": 0.0,
            "energy_regen_bonus": 0.0,
            "hunger_volatility_bonus": 0.0,
            "social_decay_bonus": 0.0,
        }

    def _get_circadian_modifiers(self, hour: int) -> dict[str, float]:
        """Get circadian modifiers for a given hour."""
        return CIRCADIAN_MODIFIERS.get(hour, {"energy": 1.0, "hunger": 1.0, "cognitive": 1.0})

    # ── Tick ────────────────────────────────────────────────────────

    async def tick(self, now: float | None = None) -> BiologyState:
        """
        Advance bio simulation by elapsed time since last tick.
        Called periodically by BioTick heartbeat module (every 5 min).

        Formula (corrected from sister):
        1. Compute elapsed ticks = (now - last_tick) / 300
        2. Compute base decay/regen per tick
        3. Apply consequence modifiers ONCE
        4. Apply circadian modifiers ONCE
        5. Multiply by ticks (CORRECT: modifiers × ticks once, not twice)
        """
        if now is None:
            now = time.time()

        state = self._state
        if state.last_tick <= 0:
            state.last_tick = now
            return state

        elapsed = now - state.last_tick
        if elapsed < 30:  # less than 30s — skip (prevents micro-tick noise)
            return state

        # Cap at 60 minutes to prevent absurd values after long downtime
        elapsed = min(elapsed, 3600.0)
        ticks = elapsed / 300.0  # number of 5-minute intervals

        cons = self._get_consequence_modifiers()
        hour = time.localtime(now).tm_hour
        circ = self._get_circadian_modifiers(hour)

        if state.is_sleeping:
            # ── Sleep regeneration ──
            energy_mod = (1.0 - cons["sleep_quality_penalty"]) + cons["energy_regen_bonus"]
            # v12.0(F): hunger > 70 reduces sleep quality
            if state.hunger > 70:
                energy_mod *= 0.6

            state.energy = min(100, state.energy + DEFAULT_SLEEP_REGEN_PER_5MIN["energy"] * energy_mod * ticks)
            state.hunger = min(100, state.hunger + DEFAULT_SLEEP_REGEN_PER_5MIN["hunger"] * ticks)
            state.social = min(100, state.social + DEFAULT_SLEEP_REGEN_PER_5MIN["social"] * ticks)
            state.cognitive = max(0, state.cognitive + DEFAULT_SLEEP_REGEN_PER_5MIN["cognitive"] * ticks)

            # Circadian half-effect during sleep
            for stat in ("energy", "hunger", "cognitive"):
                circ_mod = circ.get(stat, 1.0)
                half_effect = (circ_mod - 1.0) * 0.5 * ticks
                current = getattr(state, stat)
                setattr(state, stat, max(0, min(100, current + half_effect)))

            # Auto-wake when energy >= 90
            if state.energy >= 90:
                state.is_sleeping = False
                state.awake_since = time.strftime("%H:%M")
                log.info("Auto-woke up — energy at %.0f", state.energy)

        else:
            # ── Awake decay ──
            decay = dict(DEFAULT_DECAY_PER_5MIN)

            # Consequence modifiers
            decay["cognitive"] += cons["cognitive_decay_bonus"]
            decay["energy"] += cons["energy_regen_bonus"]
            decay["hunger"] += cons["hunger_volatility_bonus"]
            decay["social"] += cons["social_decay_bonus"]

            # v12.0(A): High hunger accelerates energy drain
            if state.hunger > 70:
                decay["energy"] -= 0.5

            # v12.0(B): Cognitive overload reduces social
            if state.cognitive > 80:
                decay["social"] -= 0.5

            # Circadian modifiers (awake: full effect)
            for stat in ("energy", "hunger", "cognitive"):
                circ_mod = circ.get(stat, 1.0)
                decay[stat] *= circ_mod

            # v12.0(E): Satiation curve — hunger slows after eating
            if state.last_meal_timestamp:
                hours_since_meal = (now - state.last_meal_timestamp) / 3600.0
                if hours_since_meal <= 2:
                    decay["hunger"] = +1.0
                elif hours_since_meal <= 3:
                    decay["hunger"] = +1.5
                elif hours_since_meal <= 4:
                    decay["hunger"] = +2.0
                else:
                    state.last_meal_timestamp = None  # satiation expired

            # Apply tick-scaled decay (CORRECT: single multiplication)
            state.energy = max(0, min(100, state.energy + decay["energy"] * ticks))
            state.hunger = max(0, min(100, state.hunger + decay["hunger"] * ticks))
            state.social = max(0, min(100, state.social + decay["social"] * ticks))
            state.cognitive = max(0, min(100, state.cognitive + decay["cognitive"] * ticks))

        state.last_tick = now
        await self.save_state()
        return state

    # ── Actions ─────────────────────────────────────────────────────

    def apply_action(self, name: str):
        """Apply an action's bio effects. All names in English."""
        effects = ACTION_EFFECTS.get(name)
        if effects is None:
            log.debug("Unknown action '%s' — no bio effect", name)
            return

        state = self._state
        for stat, delta in effects.items():
            current = getattr(state, stat)
            setattr(state, stat, max(0, min(100, current + delta)))

        # Special handling
        if name == "eat":
            state.last_ate = time.strftime("%H:%M")
            state.last_meal_timestamp = time.time()
        elif name == "sleep":
            state.is_sleeping = True

        if self._consequence_tracker:
            self._consequence_tracker.record_action(name)

        log.debug("Action '%s' applied: e=%.0f h=%.0f s=%.0f c=%.0f",
                   name, state.energy, state.hunger, state.social, state.cognitive)

    # ── Context injection ───────────────────────────────────────────

    @property
    def state(self) -> BiologyState:
        return self._state

    def get_context_hint(self) -> str:
        """Return a one-line bio summary for LLM context injection."""
        s = self._state
        parts = [
            f"Energy: {_label(s.energy, ENERGY_BANDS)} ({s.energy:.0f}/100)",
            f"Hunger: {_label(s.hunger, HUNGER_BANDS)} ({s.hunger:.0f}/100)",
            f"Social: {_label(s.social, SOCIAL_BANDS)} ({s.social:.0f}/100)",
            f"Cognitive: {_label(s.cognitive, COGNITIVE_BANDS)} ({s.cognitive:.0f}/100)",
        ]
        if s.is_sleeping:
            parts.append("💤 Currently sleeping")
        return " | ".join(parts)

    def felt_mood(self) -> str:
        """Priority-ordered mood label based on bio stats."""
        s = self._state
        if s.energy < 15:
            return "extrem müde"
        if s.hunger > 75:
            return "sehr hungrig — gereizter als sonst"
        if s.social < 5:
            return "braucht dringend Ruhe"
        if s.cognitive > 85:
            return "Kopf ist überfüllt"
        if s.energy > 75 and s.social > 60:
            return "fühlt sich eigentlich ganz gut"
        return "normaler Zustand"

    def urgency_hint(self) -> str | None:
        """Return a critical urgency hint, or None if everything is OK."""
        s = self._state
        if s.hunger > 80:
            return "CRITICAL: extremely hungry — needs to eat immediately"
        if s.energy < 10:
            return "CRITICAL: extremely exhausted — needs sleep immediately"
        if s.social < 8:
            return "CRITICAL: social battery completely empty — needs solitude"
        return None
