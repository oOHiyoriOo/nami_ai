"""
Custom Mood Module — mood swings and PAD identity drift for Nami AI.

Rewritten from the "shy lil sister" experimental codebase with:
- PAD-only (no legacy 4-axis attributes)
- Clean MoodSwings engine
- EventBus-driven identity drift
"""
from custom.mood.engine import MoodEngine
from custom.mood.identity import PADIdentityDrift, PADState

__all__ = ["MoodEngine", "PADIdentityDrift", "PADState"]
