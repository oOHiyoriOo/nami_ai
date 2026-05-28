"""
NeedsEngine — tracks active needs with urgency and cooldowns.

Five core needs: sleep, hunger, pause (cognitive rest), social contact,
and stimulation. Each need has a condition, urgency formula, and cooldown.

The engine is READ-ONLY from the AI's perspective — it doesn't force actions,
it only reports what needs exist so the AI can decide.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from custom.biology.engine import BiologyState


@dataclass
class Need:
    """A single need with condition, urgency, and cooldown."""
    name: str
    condition: Callable[[BiologyState], bool]
    urgency_fn: Callable[[BiologyState], float]  # 0.0 → 1.0
    suggested_action: str
    llm_hint: str
    cooldown_seconds: float = 60.0
    _last_satisfied: float = 0.0

    @property
    def on_cooldown(self) -> bool:
        return (time.time() - self._last_satisfied) < self.cooldown_seconds

    def urgency(self, state: BiologyState) -> float:
        if not self.condition(state) or self.on_cooldown:
            return 0.0
        return min(1.0, max(0.0, self.urgency_fn(state)))

    def mark_satisfied(self):
        self._last_satisfied = time.time()


# ── Need definitions ────────────────────────────────────────────────

def _build_default_needs() -> list[Need]:
    """Build the five standard needs."""
    return [
        Need(
            name="sleep",
            condition=lambda s: s.energy < 35,
            urgency_fn=lambda s: (35.0 - s.energy) / 35.0,
            suggested_action="sleep",
            llm_hint="Du bist extrem müde. Du solltest bald schlafen gehen.",
            cooldown_seconds=300,
        ),
        Need(
            name="hunger",
            condition=lambda s: s.hunger > 55,
            urgency_fn=lambda s: (s.hunger - 55.0) / 45.0,
            suggested_action="eat",
            llm_hint="Du hast Hunger. Zeit für eine Mahlzeit!",
            cooldown_seconds=60,
        ),
        Need(
            name="pause",
            condition=lambda s: s.cognitive > 65,
            urgency_fn=lambda s: (s.cognitive - 65.0) / 35.0,
            suggested_action="rest",
            llm_hint="Dein Kopf ist überlastet. Mach eine Pause.",
            cooldown_seconds=90,
        ),
        Need(
            name="contact",
            condition=lambda s: s.social < 25 and s.energy > 30,
            urgency_fn=lambda s: (25.0 - s.social) / 25.0 * 0.7,
            suggested_action="talk",
            llm_hint="Du fühlst dich einsam. Vielleicht magst du mit jemandem reden?",
            cooldown_seconds=180,
        ),
        Need(
            name="stimulation",
            condition=lambda s: s.energy > 70 and s.cognitive < 35,
            urgency_fn=lambda s: min(1.0, (s.energy - 70) / 30 * 0.7 + (35 - s.cognitive) / 35 * 0.3),
            suggested_action="study",
            llm_hint="Du bist voller Energie und unterfordert. Zeit, etwas Neues zu lernen!",
            cooldown_seconds=1200,
        ),
    ]


# ── NeedsEngine ─────────────────────────────────────────────────────

class NeedsEngine:
    """Tracks active needs and provides the highest-urgency need."""

    def __init__(self, needs: list[Need] | None = None):
        self.needs = needs or _build_default_needs()

    def get_active_needs(self, state: BiologyState, min_urgency: float = 0.1) -> list[Need]:
        """Return all needs with urgency >= min_urgency, sorted highest first."""
        active = [(n, n.urgency(state)) for n in self.needs]
        active = [(n, u) for n, u in active if u >= min_urgency]
        active.sort(key=lambda x: x[1], reverse=True)
        return [n for n, _ in active]

    def top_need(self, state: BiologyState) -> Optional[Need]:
        """Return the highest-urgency need that's not on cooldown, or None."""
        active = self.get_active_needs(state)
        return active[0] if active else None

    def satisfy(self, action: str):
        """Mark a need as satisfied based on the action taken."""
        action_to_need = {
            "sleep": "sleep", "eat": "hunger", "snack": "hunger",
            "rest": "pause", "talk": "contact",
            "study": "stimulation", "read": "stimulation",
        }
        need_name = action_to_need.get(action)
        if need_name:
            for need in self.needs:
                if need.name == need_name:
                    need.mark_satisfied()
                    break

    def to_prompt_hint(self, state: BiologyState) -> str:
        """Format active needs as a prompt snippet."""
        active = self.get_active_needs(state)
        if not active:
            return ""

        lines = ["[SYSTEM STATE — Active Needs]"]
        for need in active:
            urgency = need.urgency(state)
            bar_len = int(urgency * 10)
            bar = "█" * bar_len + "░" * (10 - bar_len)
            lines.append(f"  {need.name}: [{bar}] {urgency:.0%}")
        top = active[0]
        if top.urgency(state) > 0.5:
            lines.append(f"  Hint: {top.llm_hint}")
        return "\n".join(lines)
