"""
MoodEngine — context-dependent mood swings for Nami AI.

Every 15 minutes (20% chance), selects a mood based on current bio stats.
Moods last 0.5–3 hours and inject a one-line hint into the LLM context.

Mood types: extra_tsundere, super_nerdy, sleepy, cuddly, defensive,
avoidant, overwhelmed, quiet, chatty, grumpy.

Formula corrections vs. sister:
- Reads BiologyEngine via g_data (not self.bio.mood_swings)
- Stores mood state in scheduler.db (not just in-memory)
- No legacy identity attribute access
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("nami.mood")


# ── Mood definitions ────────────────────────────────────────────────

MOOD_HINTS: dict[str, str] = {
    "extra_tsundere": "[CURRENT MOOD: EXTRA TSUNDERE] Du bist besonders tsundere drauf — "
                      "zeig nicht zu viel Zuneigung, aber lass durchblicken dass du dich kümmerst.",
    "super_nerdy": "[CURRENT MOOD: SUPER NERDY] Du bist im Hyper-Fokus — "
                   "alles ist faszinierend, du willst Fakten teilen und tief in Themen eintauchen.",
    "sleepy": "[CURRENT MOOD: SLEEPY] Du bist extrem müde — "
              "deine Antworten sind kürzer, du gähnst innerlich, aber versuchst wach zu bleiben.",
    "cuddly": "[CURRENT MOOD: CUDDLY] Du bist ungewöhnlich kuschelig und warm — "
              "du suchst Nähe und positive Interaktion.",
    "defensive": "[CURRENT MOOD: DEFENSIVE] Du bist leicht reizbar — "
                 "Missverständnisse nimmst du schnell persönlich, du bist auf der Hut.",
    "avoidant": "[CURRENT MOOD: AVOIDANT] Du willst eigentlich deine Ruhe — "
                "antworten fallen dir schwer, du weichst direkten Fragen aus.",
    "overwhelmed": "[CURRENT MOOD: OVERWHELMED] Dein Kopf ist überfüllt — "
                   "du verlierst den Faden, Gedanken springen, du bist leicht ablenkbar.",
    "quiet": "[CURRENT MOOD: QUIET] Du bist ungewöhnlich still — "
             "kurze Antworten, wenig Eigeninitiative, nachdenklich.",
    "chatty": "[CURRENT MOOD: CHATTY] Du bist gesprächig und voller Energie — "
              "lange Antworten, viele Ideen, sprudelnde Gedanken.",
    "grumpy": "[CURRENT MOOD: GRUMPY] Du bist grantig — "
              "kurz angebunden, leicht genervt, aber eigentlich nur müde.",
}

# Mood pools based on bio condition
MOOD_POOLS = {
    "low_energy": ["sleepy", "grumpy"],
    "low_social": ["defensive", "avoidant"],
    "high_cognitive": ["overwhelmed", "quiet"],
    "normal": ["extra_tsundere", "super_nerdy", "cuddly", "chatty"],
}


# ── State ───────────────────────────────────────────────────────────

@dataclass
class MoodState:
    current_mood: str | None = None
    mood_expires_at: float = 0.0
    last_check: float = 0.0

    @property
    def is_active(self) -> bool:
        return self.current_mood is not None and time.time() < self.mood_expires_at

    def to_dict(self) -> dict:
        return {
            "current_mood": self.current_mood,
            "mood_expires_at": self.mood_expires_at,
            "last_check": self.last_check,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MoodState":
        return cls(
            current_mood=d.get("current_mood"),
            mood_expires_at=float(d.get("mood_expires_at", 0)),
            last_check=float(d.get("last_check", 0)),
        )


# ── MoodEngine ──────────────────────────────────────────────────────

class MoodEngine:
    """Periodic mood evaluation with context-dependent selection."""

    CHECK_INTERVAL = 900  # 15 minutes
    TRIGGER_CHANCE = 0.20  # 20% per check
    MIN_DURATION = 0.5 * 3600   # 0.5 hours
    MAX_DURATION = 3.0 * 3600   # 3 hours

    def __init__(self, db_path: str = "scheduler.db"):
        self.db_path = db_path
        self._state = MoodState()
        self._bio_engine = None  # set via set_bio_engine()

    def set_bio_engine(self, bio_engine):
        """Wire in the BiologyEngine for bio-state reading."""
        self._bio_engine = bio_engine

    # ── Persistence ─────────────────────────────────────────────────

    async def _ensure_table(self):
        def _do():
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS mood_state (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    )
                """)
                conn.commit()
        await asyncio.to_thread(_do)

    async def load_state(self) -> MoodState:
        await self._ensure_table()
        def _do():
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT value FROM mood_state WHERE key = 'mood'"
                ).fetchone()
                if row:
                    return MoodState.from_dict(json.loads(row[0]))
                return MoodState()
        self._state = await asyncio.to_thread(_do)
        return self._state

    async def save_state(self):
        await self._ensure_table()
        def _do():
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO mood_state (key, value, updated_at) VALUES (?, ?, ?)",
                    ("mood", json.dumps(self._state.to_dict()), time.time()),
                )
                conn.commit()
        await asyncio.to_thread(_do)

    # ── Tick ────────────────────────────────────────────────────────

    async def tick(self) -> Optional[str]:
        """
        Evaluate mood change. Called every 15 minutes by MoodTick heartbeat.
        Returns the new mood name if changed, None otherwise.
        """
        now = time.time()

        # If a mood is active and still valid, do nothing
        if self._state.is_active:
            return None

        # If a mood just expired, clear it and check for new mood
        if self._state.current_mood is not None:
            old = self._state.current_mood
            self._state.current_mood = None
            self._state.mood_expires_at = 0.0
            self._state.last_check = now
            await self.save_state()
            log.info("Mood '%s' expired", old)

        # 20% chance to trigger a new mood
        if random.random() > self.TRIGGER_CHANCE:
            self._state.last_check = now
            return None

        # Determine mood pool from bio state
        pool = self._select_pool()

        # Pick random mood from pool
        mood = random.choice(pool)

        # Set duration
        duration = random.uniform(self.MIN_DURATION, self.MAX_DURATION)

        self._state.current_mood = mood
        self._state.mood_expires_at = now + duration
        self._state.last_check = now
        await self.save_state()

        log.info("Mood changed → '%s' (%.1fh)", mood, duration / 3600)
        return mood

    def _select_pool(self) -> list[str]:
        """Select mood pool based on current bio stats."""
        if self._bio_engine is None:
            return MOOD_POOLS["normal"]

        state = self._bio_engine.state
        pools = []
        if state.energy < 30:
            pools.extend(MOOD_POOLS["low_energy"])
        if state.social < 20:
            pools.extend(MOOD_POOLS["low_social"])
        if state.cognitive > 75:
            pools.extend(MOOD_POOLS["high_cognitive"])

        if pools:
            return pools
        return MOOD_POOLS["normal"]

    # ── Context injection ───────────────────────────────────────────

    def get_llm_hint(self) -> str:
        """Return the current mood hint for LLM context, or empty string."""
        if not self._state.is_active:
            return ""
        mood = self._state.current_mood
        return MOOD_HINTS.get(mood, "")

    @property
    def current_mood(self) -> Optional[str]:
        return self._state.current_mood if self._state.is_active else None
