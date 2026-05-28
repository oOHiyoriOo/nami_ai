"""
Comprehensive test suite for all custom bio+mood modules.
Covers: BiologyEngine, NeedsEngine, ConsequenceTracker, MoodEngine,
        PADIdentityDrift, BioTickModule, MoodTickModule.

Run: python tests/test_custom_bio_mood_all.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT))


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load all custom modules
bio_mod = _load_module("custom.biology.engine", _PROJECT / "custom" / "biology" / "engine.py")
needs_mod = _load_module("custom.biology.needs", _PROJECT / "custom" / "biology" / "needs.py")
cons_mod = _load_module("custom.biology.consequences", _PROJECT / "custom" / "biology" / "consequences.py")
mood_mod = _load_module("custom.mood.engine", _PROJECT / "custom" / "mood" / "engine.py")
pad_mod = _load_module("custom.mood.identity", _PROJECT / "custom" / "mood" / "identity.py")
hb_mod = _load_module("custom.heartbeat_modules", _PROJECT / "custom" / "heartbeat_modules.py")

BiologyEngine = bio_mod.BiologyEngine
BiologyState = bio_mod.BiologyState
_label = bio_mod._label
ENERGY_BANDS = bio_mod.ENERGY_BANDS

NeedsEngine = needs_mod.NeedsEngine

ConsequenceTracker = cons_mod.ConsequenceTracker
ConsequenceRule = cons_mod.ConsequenceRule

MoodEngine = mood_mod.MoodEngine

PADIdentityDrift = pad_mod.PADIdentityDrift
PADState = pad_mod.PADState

BioTickModule = hb_mod.BioTickModule
MoodTickModule = hb_mod.MoodTickModule

# ── Test harness ────────────────────────────────────────────────────

_passed = 0
_failed = 0


def check(condition: bool, label: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"  ❌ FAIL: {label}")


# ── Temp DB ─────────────────────────────────────────────────────────

_db_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_db_fd)


def make_bio_engine():
    return BiologyEngine(db_path=_db_path)


# ════════════════════════════════════════════════════════════════════
# SUITE 1: BiologyState
# ════════════════════════════════════════════════════════════════════

def test_biology_state():
    print("SUITE 1: BiologyState")
    s = BiologyState()
    check(s.energy == 75.0, "default energy")
    check(s.hunger == 20.0, "default hunger")
    s2 = BiologyState.from_dict(s.to_dict())
    check(s2.energy == 75.0, "round-trip")
    check(_label(90, ENERGY_BANDS) == "voller Energie", "label top")
    check(_label(1, ENERGY_BANDS) == "kurz vor dem Einschlafen", "label bottom")


# ════════════════════════════════════════════════════════════════════
# SUITE 2: BiologyEngine — tick
# ════════════════════════════════════════════════════════════════════

def test_bio_tick():
    print("SUITE 2: BiologyEngine tick")

    async def _run():
        e = BiologyEngine(db_path=_db_path)
        await e.load_state()
        e._state.last_tick = 1000

        # Single tick awake decay
        with patch("time.time", return_value=1300), \
             patch("time.localtime", return_value=time.struct_time((2026, 5, 28, 12, 0, 0, 0, 0, 0))):
            st = await e.tick(now=1300)
        check(st.energy < 75, "tick: energy decayed")
        check(st.hunger > 20, "tick: hunger increased")
        check(st.last_tick == 1300, "tick: last_tick updated")

        # Skip micro-tick (<30s)
        e._state = BiologyState(energy=75, last_tick=1000)
        with patch("time.time", return_value=1020):
            st = await e.tick(now=1020)
        check(st.energy == 75, "tick: <30s skip")

        # Clamping at boundaries
        e._state = BiologyState(energy=1, hunger=99, last_tick=1000)
        with patch("time.time", return_value=1300), \
             patch("time.localtime", return_value=time.struct_time((2026, 5, 28, 12, 0, 0, 0, 0, 0))):
            st = await e.tick(now=1300)
        check(0 <= st.energy <= 100, "tick: energy clamped")
        check(0 <= st.hunger <= 100, "tick: hunger clamped")

        # Sleep regeneration
        e._state = BiologyState(energy=50, is_sleeping=True, last_tick=1000)
        with patch("time.time", return_value=1300), \
             patch("time.localtime", return_value=time.struct_time((2026, 5, 28, 3, 0, 0, 0, 0, 0))):
            st = await e.tick(now=1300)
        check(st.energy > 50, "tick: sleep regen")

        # Auto-wake at energy >= 90
        e._state = BiologyState(energy=89, is_sleeping=True, last_tick=1000)
        with patch("time.time", return_value=1300), \
             patch("time.localtime", return_value=time.struct_time((2026, 5, 28, 3, 0, 0, 0, 0, 0))):
            st = await e.tick(now=1300)
        check(not st.is_sleeping, "tick: auto-wake at >=90")

        # Circadian: night decay > morning decay
        e._state = BiologyState(energy=75, last_tick=1000)
        with patch("time.time", return_value=1300), \
             patch("time.localtime", return_value=time.struct_time((2026, 5, 28, 3, 0, 0, 0, 0, 0))):
            sn = await e.tick(now=1300)
        e2 = BiologyEngine(db_path=_db_path)
        await e2.load_state()
        e2._state = BiologyState(energy=75, last_tick=1000)
        with patch("time.time", return_value=1300), \
             patch("time.localtime", return_value=time.struct_time((2026, 5, 28, 8, 0, 0, 0, 0, 0))):
            sm = await e2.tick(now=1300)
        check(sn.energy < sm.energy, "circadian: night > morning decay")

        # Consequence modifier hook
        e3 = BiologyEngine(db_path=_db_path)
        await e3.load_state()
        e3._state = BiologyState(energy=75, cognitive=30, last_tick=1000)
        mt = MagicMock()
        mt.get_modifiers.return_value = {
            "cognitive_decay_bonus": 5, "sleep_quality_penalty": 0,
            "energy_regen_bonus": 0, "hunger_volatility_bonus": 0,
            "social_decay_bonus": 0,
        }
        e3.set_consequence_tracker(mt)
        with patch("time.time", return_value=1300), \
             patch("time.localtime", return_value=time.struct_time((2026, 5, 28, 12, 0, 0, 0, 0, 0))):
            await e3.tick(now=1300)
        check(mt.get_modifiers.called, "tick: consequence modifiers applied")

    asyncio.run(_run())


# ════════════════════════════════════════════════════════════════════
# SUITE 3: BiologyEngine — actions & hints
# ════════════════════════════════════════════════════════════════════

def test_bio_actions():
    print("SUITE 3: Actions & hints")

    # Eat
    e = make_bio_engine()
    e.apply_action("eat")
    check(e.state.hunger < 20, "eat: hunger down")
    check(e.state.last_meal_timestamp is not None, "eat: meal timestamp set")
    check(e.state.last_ate is not None, "eat: last_ate set")

    # Sleep
    e = make_bio_engine()
    e.apply_action("sleep")
    check(e.state.is_sleeping, "sleep: now sleeping")

    # Unknown action — no effect
    e = make_bio_engine()
    e.apply_action("unknown")
    check(e.state.energy == 75, "unknown: no effect")

    # Action clamp
    e = make_bio_engine()
    e._state.energy = 99
    e.apply_action("sleep")
    check(e.state.energy == 100, "action: clamped to 100")

    # Consequence recording
    e = make_bio_engine()
    m = MagicMock()
    e.set_consequence_tracker(m)
    e.apply_action("study")
    m.record_action.assert_called_once_with("study")

    # Context hints
    e = make_bio_engine()
    e._state = BiologyState(energy=90, hunger=5, social=80, cognitive=15)
    hint = e.get_context_hint()
    check("voller Energie" in hint, "hint: high energy")
    check("übersatt" in hint, "hint: oversated")

    # Felt mood
    check(e.felt_mood() == "fühlt sich eigentlich ganz gut", "felt_mood: good")
    e._state.energy = 10
    check(e.felt_mood() == "extrem müde", "felt_mood: exhausted")
    e._state = BiologyState(energy=75, hunger=85)
    check(e.felt_mood() == "sehr hungrig — gereizter als sonst", "felt_mood: starving")

    # Urgency hint
    e._state = BiologyState(energy=75, hunger=20, social=55, cognitive=30)
    check(e.urgency_hint() is None, "urgency: none when balanced")
    e._state.hunger = 90
    check(e.urgency_hint() is not None, "urgency: triggered at high hunger")


# ════════════════════════════════════════════════════════════════════
# SUITE 4: NeedsEngine
# ════════════════════════════════════════════════════════════════════

def test_needs_engine():
    print("SUITE 4: NeedsEngine")

    ne = NeedsEngine()
    check(len(ne.needs) == 5, "5 needs defined")
    check({n.name for n in ne.needs} == {"sleep", "hunger", "pause", "contact", "stimulation"},
          "correct need names")

    # Balanced state → no active needs
    st = BiologyState(energy=75, hunger=20, social=55, cognitive=40)
    check(len(ne.get_active_needs(st)) == 0, "no needs when balanced")

    # Trigger individual needs
    st.energy = 25
    check(any(n.name == "sleep" for n in ne.get_active_needs(st)), "sleep triggers")

    st = BiologyState(energy=75, hunger=70)
    check(any(n.name == "hunger" for n in ne.get_active_needs(st)), "hunger triggers")

    st = BiologyState(energy=75, hunger=20, social=55, cognitive=80)
    check(any(n.name == "pause" for n in ne.get_active_needs(st)), "pause triggers")

    st = BiologyState(energy=50, hunger=20, social=15, cognitive=30)
    check(any(n.name == "contact" for n in ne.get_active_needs(st)), "contact triggers")

    # Contact blocked when energy too low
    st = BiologyState(energy=20, hunger=20, social=15, cognitive=30)
    check(not any(n.name == "contact" for n in ne.get_active_needs(st)),
          "contact blocked at low energy")

    st = BiologyState(energy=80, hunger=20, social=55, cognitive=20)
    check(any(n.name == "stimulation" for n in ne.get_active_needs(st)), "stimulation triggers")

    # Cooldown via satisfy()
    ne.satisfy("sleep")
    st = BiologyState(energy=25)
    check(not any(n.name == "sleep" for n in ne.get_active_needs(st)), "cooldown blocks sleep")

    # Prompt hint (fresh engine for both needs)
    ne2 = NeedsEngine()
    st = BiologyState(energy=25, hunger=70)
    hint = ne2.to_prompt_hint(st)
    check("sleep" in hint.lower() and "hunger" in hint.lower(), "hint includes both needs")


# ════════════════════════════════════════════════════════════════════
# SUITE 5: ConsequenceTracker
# ════════════════════════════════════════════════════════════════════

def test_consequence_tracker():
    print("SUITE 5: ConsequenceTracker")

    # Rule construction
    r = ConsequenceRule("t", "study", 2, 3600, "k", 1.0, "test rule")
    check(r.window_hours == 2 and r.min_count == 3600, "rule field order")

    ct = ConsequenceTracker()
    # Rule #6 (long_fast) has min_count=0 → always applies hunger_volatility
    m0 = ct.get_modifiers()
    check(m0["hunger_volatility_bonus"] == 0.3, "hunger volatility default 0.3")
    check(m0["cognitive_decay_bonus"] == 0.0, "cognitive decay default 0")
    check(m0["sleep_quality_penalty"] == 0.0, "sleep quality default 0")

    # Action counting
    ct.record_action("study")
    ct.record_action("study")
    check(ct._count_in_window("study", 24) == 2, "count 2 studies")
    check(ct._count_in_window("eat", 24) == 0, "count 0 eats")

    # 3 studies triggers pc_marathon rule
    ct.record_action("study")
    m = ct.get_modifiers()
    check(m.get("cognitive_decay_bonus", 0) > 0, "pc_marathon triggered at 3 studies")

    # Clamp after excessive actions
    for _ in range(20):
        ct.record_action("study")
    m2 = ct.get_modifiers()
    for k, v in m2.items():
        check(-10 <= v <= 10, f"{k}={v} within clamp bounds")

    # History pruning
    ct2 = ConsequenceTracker()
    ct2._history = [(1000, "study")]
    ct2._prune_history(ct2.HISTORY_RETENTION + 1001)
    check(len(ct2._history) == 0, "history pruned after retention")

    # Rule #5 threshold fix (was 0 in sister code)
    rest_rules = [r for r in cons_mod.RULES if r.action == "rest"]
    check(all(r.min_count >= 3 for r in rest_rules), "rest rules have threshold >= 3")


# ════════════════════════════════════════════════════════════════════
# SUITE 6: MoodEngine
# ════════════════════════════════════════════════════════════════════

def test_mood_engine():
    print("SUITE 6: MoodEngine")

    me = MoodEngine(db_path=_db_path)
    check(me.current_mood is None, "initial mood is None")

    mb = MagicMock()

    # Low energy pool
    mb.state = BiologyState(energy=20)
    me.set_bio_engine(mb)
    pool = me._select_pool()
    check("sleepy" in pool or "grumpy" in pool, "low_energy pool")

    # Low social pool
    mb.state = BiologyState(energy=75, social=10)
    pool = me._select_pool()
    check("defensive" in pool or "avoidant" in pool, "low_social pool")

    # High cognitive pool
    mb.state = BiologyState(energy=75, social=55, cognitive=85)
    pool = me._select_pool()
    check("overwhelmed" in pool or "quiet" in pool, "high_cognitive pool")

    # Normal pool size
    mb.state = BiologyState(energy=75, hunger=20, social=55, cognitive=30)
    check(len(me._select_pool()) >= 4, "normal pool has >=4 moods")

    # All mood hints are descriptive
    check(all(len(mood_mod.MOOD_HINTS[k]) > 20 for k in mood_mod.MOOD_HINTS),
          "all mood hints are longer than 20 chars")


# ════════════════════════════════════════════════════════════════════
# SUITE 7: PADIdentityDrift
# ════════════════════════════════════════════════════════════════════

def test_pad_identity():
    print("SUITE 7: PADIdentityDrift")

    # Initial state uses baseline values (not zero)
    ps = PADState()
    check(ps.pleasure == 0.10, "PAD pleasure baseline 0.10")
    check(ps.arousal == 0.30, "PAD arousal baseline 0.30")
    check(ps.dominance == 0.50, "PAD dominance baseline 0.50")

    # Clamping happens inside on_interaction (no _clamp method)
    ps2 = PADState(pleasure=1.5, arousal=-2.0)
    # Force values directly — clamping tested via on_interaction
    check(ps2.pleasure == 1.5 and ps2.arousal == -2.0, "PAD state holds raw values")
    # on_interaction will clamp
    dr_test = PADIdentityDrift(state=ps2)
    dr_test.on_interaction()  # no flags — just decay + clamp
    check(-1 <= ps2.pleasure <= 1, "on_interaction clamps pleasure")
    check(-1 <= ps2.arousal <= 1, "on_interaction clamps arousal")

    # Warm interaction increases pleasure
    dr = PADIdentityDrift()
    dr.on_interaction(was_warm=True)
    check(dr._state.pleasure > 0.10, "warm interaction → pleasure above baseline")

    # Accumulates
    p1 = dr._state.pleasure
    dr.on_interaction(was_warm=True)
    check(dr._state.pleasure > p1, "pleasure accumulates")

    # Clamp after many warm interactions
    for _ in range(200):
        dr.on_interaction(was_warm=True)
    check(-1 <= dr._state.pleasure <= 1, "clamped after many interactions")

    # Decay toward baseline is built into on_interaction
    dr2 = PADIdentityDrift()
    dr2._state.pleasure = 0.5  # set above baseline
    dr2.on_interaction()  # calls decay: pleasure += (0.10 - pleasure) * 0.01
    check(dr2._state.pleasure < 0.5, "pleasure decays toward baseline")

    # LLM hint
    check(len(dr2.get_llm_hint()) > 0, "llm hint non-empty")

    # State property returns same object (mutable)
    dr3 = PADIdentityDrift()
    sc = dr3.state
    check(sc is dr3._state, "state property returns same object")


# ════════════════════════════════════════════════════════════════════
# SUITE 8: Heartbeat Modules
# ════════════════════════════════════════════════════════════════════

async def test_heartbeat_modules():
    print("SUITE 8: Heartbeat Modules")

    # BioTickModule defaults
    bt = BioTickModule(bio_engine=MagicMock())
    check(bt.name == "bio_tick", "BioTick name")
    check(bt.priority == 60, "BioTick priority")
    check(bt.cooldown_seconds == 300, "BioTick cooldown")
    check(bt.enabled is True, "BioTick enabled default")
    check(await bt.condition(), "BioTick condition returns True")

    # BioTick stores config dict (not auto-wired to class attrs)
    bt_cfg = BioTickModule(bio_engine=MagicMock(), config={"tick_interval_seconds": 600})
    check(bt_cfg._cfg == {"tick_interval_seconds": 600}, "BioTick stores config dict")

    # MoodTickModule defaults
    mt = MoodTickModule(mood_engine=MagicMock())
    check(mt.name == "mood_tick", "MoodTick name")
    check(mt.priority == 55, "MoodTick priority")
    check(mt.cooldown_seconds == 900, "MoodTick cooldown")
    check(mt.enabled is True, "MoodTick enabled default")
    check(await mt.condition(), "MoodTick condition returns True")

    # MoodTick stores config dict
    mt_cfg = MoodTickModule(mood_engine=MagicMock(), config={"tick_interval_seconds": 1800})
    check(mt_cfg._cfg == {"tick_interval_seconds": 1800}, "MoodTick stores config dict")

    # Action delegation
    mb = MagicMock()
    mb.tick = AsyncMock()
    bt3 = BioTickModule(bio_engine=mb)
    await bt3.action()
    check(mb.tick.called, "BioTick action delegates to bio_engine.tick()")


# ════════════════════════════════════════════════════════════════════
# Runner
# ════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    test_biology_state()
    test_bio_tick()
    test_bio_actions()
    test_needs_engine()
    test_consequence_tracker()
    test_mood_engine()
    test_pad_identity()
    asyncio.run(test_heartbeat_modules())

    total = _passed + _failed
    print(f"\n{'=' * 50}")
    if _failed == 0:
        print(f"ALL {_passed} TESTS PASSED ✅")
    else:
        print(f"{_passed}/{total} passed — {_failed} FAILED ❌")
    print(f"{'=' * 50}")

    os.unlink(_db_path)
