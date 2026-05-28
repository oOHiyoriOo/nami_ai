"""
PAD Identity Drift — 3D personality model (Pleasure-Arousal-Dominance).

The PAD model tracks three orthogonal personality axes that slowly shift
with every interaction. This replaces the old 4-axis (warmth/openness/
defensiveness/nerd_intensity) model with a cleaner psychology-backed system.

PAD ranges: [-1.0, +1.0] for each axis.
Learning rate: ALPHA = 0.04 per interaction.

Formula corrections vs. sister:
- No legacy attribute access (.warmth / .defensiveness / .openness)
- Clean PAD-only implementation
- Pleasure decay toward baseline always applied
"""
from __future__ import annotations

import time
from dataclasses import dataclass

# ── Constants ───────────────────────────────────────────────────────

ALPHA = 0.04  # learning rate per interaction
BASELINE_PLEASURE = 0.10  # happiness baseline (slightly positive)
BASELINE_AROUSAL = 0.30   # energy baseline (slightly alert)
BASELINE_DOMINANCE = 0.50  # confidence baseline (moderately self-assured)

# ── State ───────────────────────────────────────────────────────────

@dataclass
class PADState:
    """Pleasure-Arousal-Dominance personality state. All clamped to [-1, +1]."""
    pleasure: float = BASELINE_PLEASURE
    arousal: float = BASELINE_AROUSAL
    dominance: float = BASELINE_DOMINANCE
    total_interactions: int = 0
    last_update: float = 0.0

    def to_dict(self) -> dict:
        return {
            "pleasure": self.pleasure, "arousal": self.arousal,
            "dominance": self.dominance,
            "total_interactions": self.total_interactions,
            "last_update": self.last_update,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PADState":
        return cls(
            pleasure=float(d.get("pleasure", BASELINE_PLEASURE)),
            arousal=float(d.get("arousal", BASELINE_AROUSAL)),
            dominance=float(d.get("dominance", BASELINE_DOMINANCE)),
            total_interactions=int(d.get("total_interactions", 0)),
            last_update=float(d.get("last_update", 0.0)),
        )


# ── Identity Drift ─────────────────────────────────────────────────

class PADIdentityDrift:
    """Manages PAD personality shifts across interactions."""

    def __init__(self, state: PADState | None = None):
        self._state = state or PADState()

    @property
    def state(self) -> PADState:
        return self._state

    # ── Shifts ──────────────────────────────────────────────────────

    def on_interaction(self, *,
                       topic_excited: bool = False,
                       was_warm: bool = False,
                       small_talk: bool = False,
                       long_solo_session: bool = False):
        """
        Apply PAD shifts based on interaction quality.
        Multiple flags can be True simultaneously — all shifts apply.

        Shift formulas (corrected, PAD-only):
        - topic_excited (nerd topic): pleasure +0.08, arousal +0.10, dominance -0.02
        - was_warm (warm exchange): pleasure +0.06, dominance -0.04
        - small_talk (boring): arousal -0.02
        - long_solo_session (isolation): pleasure -0.04, dominance +0.02
        - Always: pleasure decays toward baseline (0.10)
        """
        s = self._state
        now = time.time()

        if topic_excited:
            s.pleasure += ALPHA * 2.0    # +0.08
            s.arousal += ALPHA * 2.5     # +0.10
            s.dominance -= ALPHA * 0.5   # -0.02

        if was_warm:
            s.pleasure += ALPHA * 1.5    # +0.06
            s.dominance -= ALPHA * 1.0   # -0.04

        if small_talk:
            s.arousal -= ALPHA * 0.5     # -0.02

        if long_solo_session:
            s.pleasure -= ALPHA * 1.0    # -0.04
            s.dominance += ALPHA * 0.5   # +0.02

        # Passive pleasure decay toward baseline
        s.pleasure += (BASELINE_PLEASURE - s.pleasure) * 0.01

        # Clamp to [-1, +1]
        s.pleasure = max(-1.0, min(1.0, s.pleasure))
        s.arousal = max(-1.0, min(1.0, s.arousal))
        s.dominance = max(-1.0, min(1.0, s.dominance))

        s.total_interactions += 1
        s.last_update = now

    # ── Queries ─────────────────────────────────────────────────────

    def get_llm_hint(self) -> str:
        """Return a short identity label for LLM context injection."""
        s = self._state
        p_label = self._pad_word(s.pleasure, ["frustriert", "neutral", "neugierig", "froh"])
        a_label = self._pad_word(s.arousal, ["träge", "ruhig", "aufmerksam", "aufgewühlt"])
        d_label = self._pad_word(s.dominance, ["weich", "normal", "selbstsicher", "dominant"])
        return f"[IDENTITY: {p_label}, {a_label}, {d_label}]"

    def get_nerd_threshold(self) -> float:
        """Higher arousal → lower threshold → easier to nerd out."""
        return max(0.5, 1.0 - self._state.arousal * 0.3)

    @staticmethod
    def _pad_word(val: float, labels: list[str]) -> str:
        """Map PAD value to label word using 4-band quantization."""
        idx = int((val + 1.0) / 2.0 * (len(labels) - 1) + 0.5)
        return labels[max(0, min(len(labels) - 1, idx))]
