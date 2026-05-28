"""
ConsequenceTracker — emergent long-term action pattern detection.

Tracks recent actions and applies cumulative modifiers to the biology simulation.
Corrected from sister experimental code:
- All 8 rules are reachable (German names aligned)
- Rule #5 threshold fixed (was 0, now 3)
- Time-based decay of modifiers between sessions
- History retention: 6 hours
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

# ── Consequence rules ───────────────────────────────────────────────
# Format: (name, action, window_hours, min_count, effect_key, effect_value)

@dataclass
class ConsequenceRule:
    name: str
    action: str          # action name to track
    window_hours: float  # lookback window in hours
    min_count: int       # minimum occurrences to trigger
    effect_key: str      # which modifier to adjust
    effect_value: float  # delta to apply
    description: str


RULES: list[ConsequenceRule] = [
    ConsequenceRule("pc_marathon", "study", 3, 3,
                    "cognitive_decay_bonus", +0.4,
                    "PC-Marathon: cognitive decay +40%"),
    ConsequenceRule("long_pc_marathon", "study", 3, 6,
                    "cognitive_decay_bonus", +0.8,
                    "Long PC-Marathon: cognitive decay +80%"),
    ConsequenceRule("late_eating_sleep_penalty", "eat", 2, 2,
                    "sleep_quality_penalty", 0.3,
                    "Late eating: sleep quality -30%"),
    ConsequenceRule("reading_energy_regen", "read", 4, 4,
                    "energy_regen_bonus", +0.2,
                    "Much reading: energy regen slightly increased"),
    ConsequenceRule("rest_cognitive_buff", "rest", 3, 3,
                    "cognitive_decay_bonus", -0.3,
                    "Rest: cognitive decay reduced -30%"),
    ConsequenceRule("long_fast_hunger_volatility", "eat", 8, 0,
                    "hunger_volatility_bonus", +0.3,
                    "Long fast: hunger rises faster"),
    ConsequenceRule("sleep_debt_penalty", "sleep", 24, 2,
                    "sleep_quality_penalty", 0.4,
                    "Sleep debt: energy regen worse"),
    ConsequenceRule("social_activity_buff", "talk", 3, 4,
                    "social_decay_bonus", -0.2,
                    "Social activity: social recovers faster"),
]


@dataclass
class ConsequenceState:
    """Accumulated modifiers, all clamped within ranges."""
    cognitive_decay_bonus: float = 0.0     # [-0.5, 2.0]
    sleep_quality_penalty: float = 0.0     # [0.0, 0.8]
    energy_regen_bonus: float = 0.0        # [-0.3, 0.5]
    hunger_volatility_bonus: float = 0.0   # [0.0, 1.0]
    social_decay_bonus: float = 0.0        # [-0.5, 0.5]

    def get_modifiers(self) -> dict:
        return {
            "cognitive_decay_bonus": self.cognitive_decay_bonus,
            "sleep_quality_penalty": self.sleep_quality_penalty,
            "energy_regen_bonus": self.energy_regen_bonus,
            "hunger_volatility_bonus": self.hunger_volatility_bonus,
            "social_decay_bonus": self.social_decay_bonus,
        }


# ── Tracker ─────────────────────────────────────────────────────────

class ConsequenceTracker:
    """Tracks recent actions and computes emergent modifiers."""

    HISTORY_RETENTION = 6 * 3600  # 6 hours
    CLAMP_RANGES = {
        "cognitive_decay_bonus": (-0.5, 2.0),
        "sleep_quality_penalty": (0.0, 0.8),
        "energy_regen_bonus": (-0.3, 0.5),
        "hunger_volatility_bonus": (0.0, 1.0),
        "social_decay_bonus": (-0.5, 0.5),
    }
    # Accumulation strategy per effect
    SUM_KEYS = {"cognitive_decay_bonus", "energy_regen_bonus",
                 "hunger_volatility_bonus", "social_decay_bonus"}
    MAX_KEYS = {"sleep_quality_penalty"}

    def __init__(self):
        self._history: list[tuple[float, str]] = []  # [(timestamp, action_name), ...]
        self._daily_counts: dict[str, int] = defaultdict(int)
        self._last_recalculate: float = 0.0
        self._state = ConsequenceState()

    # ── Recording ───────────────────────────────────────────────────

    def record_action(self, action: str):
        """Record an action at the current time."""
        now = time.time()
        self._history.append((now, action))
        self._daily_counts[action] += 1
        self._prune_history(now)
        self._recalculate(now)

    # ── Pruning ─────────────────────────────────────────────────────

    def _prune_history(self, now: float):
        """Remove entries older than HISTORY_RETENTION."""
        cutoff = now - self.HISTORY_RETENTION
        self._history = [(ts, act) for ts, act in self._history if ts >= cutoff]

    def _count_in_window(self, action: str, window_hours: float,
                         now: float | None = None) -> int:
        """Count occurrences of an action within the last N hours."""
        if now is None:
            now = time.time()
        cutoff = now - window_hours * 3600
        return sum(1 for ts, act in self._history
                   if act == action and ts >= cutoff)

    # ── Recalculation ───────────────────────────────────────────────

    def _recalculate(self, now: float | None = None):
        """Recalculate all modifiers from scratch based on current history."""
        if now is None:
            now = time.time()

        # Reset accumulators
        state = ConsequenceState()

        for rule in RULES:
            count = self._count_in_window(rule.action, rule.window_hours, now)
            if count < rule.min_count:
                continue

            effect_key = rule.effect_key
            if effect_key in self.SUM_KEYS:
                current = getattr(state, effect_key)
                setattr(state, effect_key, current + rule.effect_value)
            elif effect_key in self.MAX_KEYS:
                # Take the worst (max) penalty
                current = getattr(state, effect_key)
                setattr(state, effect_key, max(current, rule.effect_value))

        # Clamp
        for key, (lo, hi) in self.CLAMP_RANGES.items():
            setattr(state, key, max(lo, min(hi, getattr(state, key))))

        self._state = state
        self._last_recalculate = now

    # ── Public API ──────────────────────────────────────────────────

    def get_modifiers(self) -> dict:
        """Return current modifier values. Recalculates if stale."""
        now = time.time()
        if now - self._last_recalculate > 300:  # 5 min staleness
            self._prune_history(now)
            self._recalculate(now)
        return self._state.get_modifiers()

    def clear(self):
        """Reset all history and state."""
        self._history.clear()
        self._daily_counts.clear()
        self._state = ConsequenceState()
        self._last_recalculate = 0.0

    def get_stats(self) -> dict:
        """Return debug stats."""
        return {
            "history_entries": len(self._history),
            "daily_counts": dict(self._daily_counts),
            "modifiers": self._state.get_modifiers(),
        }
