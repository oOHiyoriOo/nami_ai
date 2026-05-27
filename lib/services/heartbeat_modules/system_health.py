"""
system_health.py — HeartbeatModule: proactive system health checks.

Verifies on each tick:
1. Neo4j connectivity (RETURN 1 with timing)
2. Provider availability (ping each configured provider)
3. Adapter status (are Discord/WhatsApp adapters connected?)
4. Sandbox SSH connectivity (if enabled)
5. Memory stats (query MemoryAnalytics for degradation signals)

State tracking per check:
  healthy → degraded (1-2 consecutive failures) → unhealthy (3+ failures)
  degraded → healthy (1 success)  — recovery: degraded clears after 1 pass
  unhealthy → healthy (1 success) — recovery: unhealthy clears after 1 pass

Logs state transitions at WARNING level with Nami-style personal messages.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum

from lib.global_registry import g_data
from lib.services.heartbeat_module import HeartbeatModule


class HealthState(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class CheckState:
    """Per-check state tracker with consecutive failure counting."""

    name: str
    state: HealthState = HealthState.HEALTHY
    consecutive_failures: int = 0
    last_ok: bool = True
    last_latency_ms: float = 0.0
    last_error: str | None = None

    def record(self, ok: bool, latency_ms: float = 0.0, error: str | None = None) -> HealthState | None:
        """Record a check result. Returns the new state if it changed, else None."""
        old_state = self.state
        self.last_latency_ms = latency_ms

        if ok:
            self.consecutive_failures = 0
            self.last_ok = True
            self.last_error = None
            if self.state != HealthState.HEALTHY:
                self.state = HealthState.HEALTHY
        else:
            self.consecutive_failures += 1
            self.last_ok = False
            self.last_error = error
            if self.consecutive_failures >= 3:
                self.state = HealthState.UNHEALTHY
            elif self.consecutive_failures >= 1:
                self.state = HealthState.DEGRADED

        return self.state if self.state != old_state else None


# Nami-style personal messages for state transitions
_STATE_MESSAGES: dict[tuple[str, str, str], str] = {
    # (check_name, old_state, new_state)
    ("neo4j", "healthy", "degraded"):
        "Meine Erinnerungen scheinen kurz zu stocken… ich versuche es weiter.",
    ("neo4j", "healthy", "unhealthy"):
        "Hey, meine Erinnerungen sind gerade nicht erreichbar. Ich melde mich wenn's wieder geht.",
    ("neo4j", "degraded", "unhealthy"):
        "Ich habe jetzt mehrfach keinen Zugriff auf meine Erinnerungen. Bitte schau mal nach Neo4j.",
    ("neo4j", "unhealthy", "healthy"):
        "Meine Erinnerungen sind wieder da! Alles wieder gut.",
    ("neo4j", "degraded", "healthy"):
        "Meine Erinnerungen funktionieren wieder — war wohl nur ein kurzer Aussetzer.",
    ("providers", "healthy", "degraded"):
        "Ich brauche heute etwas länger zum Nachdenken, meine Denk-Engine ist träge.",
    ("providers", "degraded", "unhealthy"):
        "Ich kann gerade gar nicht denken — kein Provider antwortet. Bin gleich wieder da.",
    ("providers", "unhealthy", "healthy"):
        "Meine Denk-Engine läuft wieder! Wo waren wir?",
    ("providers", "degraded", "healthy"):
        "Ich bin wieder voll da — der Provider war nur kurz schwer erreichbar.",
    ("adapters", "healthy", "degraded"):
        "Ich merke gerade, dass eine meiner Verbindungen nach draußen wackelt.",
    ("adapters", "degraded", "unhealthy"):
        "Ich habe meine Verbindung nach draußen verloren — Discord/WhatsApp sind gerade nicht erreichbar.",
    ("adapters", "unhealthy", "healthy"):
        "Verbindung nach draußen ist wieder da! Ich kann wieder auf allen Kanälen antworten.",
    ("sandbox", "healthy", "degraded"):
        "Meine Sandbox-Umgebung reagiert gerade langsam.",
    ("sandbox", "degraded", "unhealthy"):
        "Ich komme nicht mehr in meine Sandbox — ich kann gerade keine Befehle ausführen.",
    ("sandbox", "unhealthy", "healthy"):
        "Sandbox ist wieder erreichbar. Alles bereit!",
    ("memory_stats", "healthy", "degraded"):
        "Mir ist aufgefallen dass viele alte Erinnerungen verblassen. Soll ich die wichtigen konsolidieren?",
    ("memory_stats", "degraded", "unhealthy"):
        "Mein Gedächtnis braucht dringend Pflege — zu viele ungenutzte und alte Erinnerungen.",
    ("memory_stats", "unhealthy", "healthy"):
        "Mein Gedächtnis ist wieder in gutem Zustand. Danke!",
}


def _nami_message(check_name: str, old_state: str, new_state: str) -> str | None:
    """Return a Nami-style personal message for a state transition, or None."""
    return _STATE_MESSAGES.get((check_name, old_state, new_state))


class SystemHealthCheck(HeartbeatModule):
    """Proactive system health verification with per-check state tracking."""

    name = "system_health"
    priority = 100  # Highest priority — check health before anything else
    cooldown_seconds = 300  # Default: every 5 minutes (overridable via config)

    def __init__(self) -> None:
        super().__init__()
        self._checks: dict[str, CheckState] = {
            "neo4j": CheckState("neo4j"),
            "providers": CheckState("providers"),
            "adapters": CheckState("adapters"),
            "sandbox": CheckState("sandbox"),
            "memory_stats": CheckState("memory_stats"),
        }
        self._last_run_results: dict = {}

    def _get_module_config(self) -> dict:
        """Read system_health module config from the heartbeat section."""
        cfg = g_data.get("cfg")
        if not cfg:
            return {}
        hb_cfg = cfg.data.get("heartbeat", {})
        modules = hb_cfg.get("modules", {})
        return modules.get("system_health", {})

    async def condition(self) -> bool:
        """Always check health — condition is the health test itself."""
        return True

    async def action(self) -> None:
        """Run all health checks with per-check state tracking."""
        mod_cfg = self._get_module_config()
        neo4j_timeout = mod_cfg.get("neo4j_timeout_ms", 5000) / 1000.0
        provider_timeout = mod_cfg.get("provider_timeout_s", 10)

        # 1. Neo4j connectivity (with timing)
        await self._run_check("neo4j", self._check_neo4j(neo4j_timeout))

        # 2. Provider availability (with per-provider timing)
        await self._run_check("providers", self._check_providers(provider_timeout))

        # 3. Adapter status
        await self._run_check("adapters", self._check_adapters())

        # 4. Sandbox SSH connectivity (if enabled)
        await self._run_check("sandbox", self._check_sandbox())

        # 5. Memory degradation signals
        await self._run_check("memory_stats", self._check_memory_stats())

    async def _run_check(self, name: str, coro) -> None:
        """Execute a check coroutine, record timing and state transitions."""
        check = self._checks[name]
        t0 = time.monotonic()
        try:
            ok, detail = await coro
        except Exception as e:
            ok, detail = False, str(e)
        latency_ms = (time.monotonic() - t0) * 1000

        old_state = check.state.value
        new_state = check.record(ok, latency_ms, error=None if ok else detail)

        self._last_run_results[name] = {
            "ok": ok,
            "state": check.state.value,
            "latency_ms": round(latency_ms, 1),
            "detail": detail,
            "consecutive_failures": check.consecutive_failures,
        }

        if new_state:
            msg = _nami_message(name, old_state, new_state.value)
            if msg:
                logging.warning(f"[heartbeat.system_health] [{name}] {msg}")
            extra = f": {detail}" if not ok and detail else ""
            logging.warning(
                f"[heartbeat.system_health] [{name}] State transition: "
                f"{old_state} → {new_state.value} "
                f"(latency={latency_ms:.1f}ms, consecutive_failures={check.consecutive_failures}){extra}"
            )
        elif not ok:
            logging.warning(
                f"[heartbeat.system_health] [{name}] {check.state.value} "
                f"(failure #{check.consecutive_failures}, latency={latency_ms:.1f}ms): {detail}"
            )
        else:
            logging.debug(
                f"[heartbeat.system_health] [{name}] healthy (latency={latency_ms:.1f}ms)"
            )

    async def _check_neo4j(self, timeout_s: float) -> tuple[bool, str]:
        """Verify Neo4j is reachable and responsive, with timing."""
        memory_db = g_data.get("memory_db")
        if not memory_db:
            return False, "memory_db not in g_data"

        try:
            driver = memory_db.get_driver()
            async with driver.session() as session:
                result = await asyncio.wait_for(
                    session.run("RETURN 1 AS n"), timeout=timeout_s
                )
                record = await asyncio.wait_for(result.single(), timeout=timeout_s)
                ok = record is not None and record["n"] == 1
                if not ok:
                    return False, "unexpected query result"
                return True, "ok"
        except asyncio.TimeoutError:
            return False, f"timed out after {timeout_s:.1f}s"
        except Exception as e:
            return False, str(e)[:200]

    async def _check_providers(self, timeout_s: float) -> tuple[bool, str]:
        """Check that all configured AI providers are responsive."""
        cfg = g_data.get("cfg")
        if not cfg:
            return False, "no config available"

        providers_config = cfg.data.get("providers", {})
        if not providers_config:
            return True, "no providers configured"

        results = {}
        all_ok = True
        for name, provider_cfg in providers_config.items():
            try:
                from lib.ai_providers import ProviderRegistry
                provider = ProviderRegistry.get_provider(name, provider_cfg)
                # list_models() is synchronous — run in thread to keep the event
                # loop unblocked and to allow asyncio.wait_for timeout enforcement.
                models = await asyncio.wait_for(
                    asyncio.to_thread(provider.list_models), timeout=timeout_s
                )
                if not models:
                    results[name] = "empty model list"
                    all_ok = False
                else:
                    results[name] = f"{len(models)} models"
            except asyncio.TimeoutError:
                results[name] = f"timed out after {timeout_s}s"
                all_ok = False
            except Exception as e:
                results[name] = str(e)[:100]
                all_ok = False

        detail = "; ".join(f"{k}: {v}" for k, v in results.items())
        return all_ok, detail

    async def _check_adapters(self) -> tuple[bool, str]:
        """Check which adapters are currently connected via WebSocket."""
        ws_server = g_data.get("adapter_ws_server")
        if not ws_server:
            return True, "no adapter WS server configured"

        connected = ws_server.connected_adapters
        if not connected:
            return True, "no adapters connected yet (waiting for bridges)"

        return True, f"connected: {', '.join(connected)}"

    async def _check_sandbox(self) -> tuple[bool, str]:
        """Verify sandbox SSH connectivity if sandbox is configured."""
        sandbox = g_data.get("sandbox_manager")
        if not sandbox:
            return True, "sandbox not configured"

        try:
            import asyncssh
            kwargs = sandbox._get_connect_kwargs()
            # Use async with (same pattern as sandbox_manager.py) — asyncssh.connect()
            # is an async context manager; awaiting it directly can fail in some versions.
            # connect_timeout applies to the TCP+SSH handshake phase.
            async with asyncssh.connect(**kwargs, connect_timeout=10.0) as conn:
                result = await asyncio.wait_for(
                    conn.run("echo ok", check=False), timeout=5.0
                )
                if result.exit_status == 0 and "ok" in (result.stdout or ""):
                    return True, "ssh connected"
                return False, f"unexpected ssh response (exit={result.exit_status})"
        except asyncio.TimeoutError:
            return False, "ssh connection timed out"
        except ImportError:
            return True, "asyncssh not installed — sandbox check skipped"
        except Exception as e:
            return False, str(e)[:200]

    async def _check_memory_stats(self) -> tuple[bool, str]:
        """Query MemoryAnalytics for degradation signals."""
        analytics = g_data.get("memory_analytics")
        if not analytics:
            return True, "memory_analytics not available"

        try:
            diagnosis = await analytics.diagnose_issues()
            health_score = diagnosis.get("health_score", 100)
            severity = diagnosis.get("severity", "low")

            if severity == "high":
                issues = diagnosis.get("issues", [])
                return False, f"health_score={health_score}, issues: {', '.join(issues[:3])}"
            elif severity == "medium":
                issues = diagnosis.get("issues", [])
                return True, f"health_score={health_score} (medium), issues: {', '.join(issues[:2])}"
            else:
                return True, f"health_score={health_score} (healthy)"
        except Exception as e:
            return True, f"memory_analytics query failed (non-critical): {e}"

    def report(self) -> dict:
        """Return structured health report with per-check detail and summary."""
        check_reports = {}
        any_unhealthy = False
        any_degraded = False

        for name, check in self._checks.items():
            last = self._last_run_results.get(name, {})
            check_reports[name] = {
                "state": check.state.value,
                "consecutive_failures": check.consecutive_failures,
                "last_latency_ms": round(check.last_latency_ms, 1),
                "last_ok": last.get("ok"),
                "last_detail": last.get("detail"),
                "last_error": check.last_error,
            }
            if check.state == HealthState.UNHEALTHY:
                any_unhealthy = True
            elif check.state == HealthState.DEGRADED:
                any_degraded = True

        if any_unhealthy:
            overall = "unhealthy"
        elif any_degraded:
            overall = "degraded"
        else:
            overall = "healthy"

        return {
            "overall": overall,
            "last_run_ago": self.seconds_since_last_run(),
            "checks": check_reports,
        }

    @property
    def status(self) -> dict:
        """Return last health check snapshot (backward compat + enriched)."""
        return self.report()
