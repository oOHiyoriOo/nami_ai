"""
sandbox_manager.py — SSH-based sandboxed execution environment.

Manages long-running commands in an isolated Docker container via asyncssh.
Commands that exceed the foreground timeout are auto-backgrounded and tracked
by job ID. Completed jobs are surfaced on the next conversation turn via
context injection.

Design notes:
- No PTY allocated → TUI apps (htop, vim) fail immediately
- stdin → /dev/null → blocking reads die at once
- Shell-level `timeout N` as additional safety net
- All state is in-memory; a sandbox reset wipes everything
"""

import asyncio
import logging
import os
import secrets
import shlex
from collections.abc import Callable
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path


def _resolve_secret(
    env_var: str,
    secrets_file: str,
    extra_val: str | None = None,
    validate: Callable[[str], bool] | None = None,
    path_mode: bool = False,
) -> str | None:
    """Resolve a secret from environment, file, or explicit value.

    1. ``env_var`` environment variable (highest priority).
    2. ``secrets_file`` on the filesystem.
    3. ``extra_val`` explicit fallback.

    When *path_mode* is True, the secrets file path itself is returned
    (used for SSH key and known_hosts file paths).  Otherwise the file
    content is read and returned (used for password secrets).

    When *validate* is provided it must receive the candidate string and
    return ``True`` for valid values; invalid values are skipped so that
    the next source is tried.
    """
    env_val = os.environ.get(env_var, "").strip()
    if env_val and (validate is None or validate(env_val)):
        return env_val

    sf = Path(secrets_file)
    try:
        if sf.is_file():
            if path_mode:
                return str(sf)
            val = sf.read_text().strip()
            if val and (validate is None or validate(val)):
                return val
    except OSError:
        pass

    if extra_val and extra_val.strip():
        return extra_val.strip()

    return None


def get_sandbox_password(config_password: str | None = None) -> str | None:
    """Resolve the sandbox SSH password from environment, secrets file, or config."""
    return _resolve_secret(
        "SANDBOX_PASSWORD",
        "/secrets/sandbox_password",
        extra_val=config_password,
    )


def get_sandbox_ssh_key() -> str | None:
    """Resolve the sandbox SSH private key path from environment or secrets file."""
    return _resolve_secret(
        "SANDBOX_SSH_KEY",
        "/secrets/sandbox_ssh_key",
        validate=lambda p: Path(p).is_file(),
        path_mode=True,
    )


def get_known_hosts_path() -> str | None:
    """Resolve the sandbox known_hosts file path from environment or secrets file."""
    return _resolve_secret(
        "SANDBOX_KNOWN_HOSTS",
        "/secrets/known_hosts",
        validate=lambda p: Path(p).is_file(),
        path_mode=True,
    )


class SandboxJob:
    """Tracks a single background command execution."""

    def __init__(self, job_id: str, command: str):
        self.job_id = job_id
        self.command = command
        self.started_at = datetime.now(timezone.utc)
        self.finished_at: datetime | None = None
        self.output = StringIO()
        self.exit_code: int | None = None
        self.task: asyncio.Task | None = None
        self.notified = False  # True once injected into a context turn

    @property
    def is_running(self) -> bool:
        return self.finished_at is None

    def get_output(self) -> str:
        return self.output.getvalue()

    def elapsed_seconds(self) -> float:
        end = self.finished_at or datetime.now(timezone.utc)
        return (end - self.started_at).total_seconds()


class SandboxManager:
    """
    Manages SSH connections to the sandbox container and tracks background jobs.

    Args:
        host:            Hostname of the sandbox container (Docker service name).
        port:            SSH port (default 22).
        username:        SSH username (default "root").
        password:        SSH password (fallback if key auth unavailable).
        ssh_key_path:    Path to SSH private key for key-based authentication (preferred).
        fg_timeout:      Seconds before a command is auto-backgrounded (default 15).
        max_output_kb:   Maximum output buffer size per job in KB (default 16).
    """

    _SHELL_TIMEOUT_SECONDS = 600  # 10 minutes

    def __init__(
        self,
        host: str = "sandbox",
        port: int = 22,
        username: str = "root",
        password: str | None = None,
        ssh_key_path: str | None = None,
        fg_timeout: float = 15.0,
        max_output_kb: int = 16,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.ssh_key_path = ssh_key_path
        self.fg_timeout = fg_timeout
        self.max_output_bytes = max_output_kb * 1024
        self._jobs: dict[str, SandboxJob] = {}

    def _get_connect_kwargs(self) -> dict:
        """Build kwargs for asyncssh.connect, preferring key-based auth over password.

        Host key verification is intentionally disabled (known_hosts=None) — the
        sandbox runs on the same internal Docker network, so MITM is not a concern.
        """
        kwargs: dict = {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "known_hosts": None,
        }
        if self.ssh_key_path and Path(self.ssh_key_path).is_file():
            kwargs["client_keys"] = [self.ssh_key_path]
        elif self.password:
            kwargs["password"] = self.password
        return kwargs

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, command: str) -> dict:
        """
        Execute a command in the sandbox.

        Waits up to fg_timeout seconds for a result. If the command is still
        running at that point it is detached into the background and a job_id
        is returned so the caller can poll later.

        Returns:
            Foreground finish: {"status": "done", "exit_code": int, "output": str}
            Auto-backgrounded: {"status": "running", "job_id": str, "output_so_far": str}
            Error:             {"status": "error", "message": str}
        """
        job_id = secrets.token_hex(4)
        job = SandboxJob(job_id, command)
        self._jobs[job_id] = job

        task = asyncio.create_task(self._run_job(job))
        job.task = task

        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=self.fg_timeout)
            # Finished within timeout — return immediately
            return {
                "status": "done",
                "exit_code": job.exit_code,
                "output": _truncate(job.get_output(), self.max_output_bytes),
            }
        except asyncio.TimeoutError:
            # Still running — auto-background
            logging.info(f"[sandbox] Job {job_id} auto-backgrounded: {command[:60]}")
            return {
                "status": "running",
                "job_id": job_id,
                "output_so_far": _truncate(job.get_output(), self.max_output_bytes),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_output(self, job_id: str) -> dict:
        """
        Return current output and status of a background job.

        Returns:
            {"status": "running"|"done"|"not_found", "output": str, "exit_code": int|None}
        """
        job = self._jobs.get(job_id)
        if not job:
            return {"status": "not_found", "job_id": job_id}
        return {
            "status": "running" if job.is_running else "done",
            "job_id": job_id,
            "command": job.command,
            "output": _truncate(job.get_output(), self.max_output_bytes),
            "exit_code": job.exit_code,
            "elapsed_seconds": round(job.elapsed_seconds(), 1),
        }

    def kill_job(self, job_id: str) -> dict:
        """Cancel a running background job."""
        job = self._jobs.get(job_id)
        if not job:
            return {"status": "not_found", "job_id": job_id}
        if not job.is_running:
            return {"status": "already_done", "job_id": job_id}
        if job.task:
            job.task.cancel()
        job.finished_at = datetime.now(timezone.utc)
        return {"status": "killed", "job_id": job_id}

    def list_jobs(self) -> list[dict]:
        """Return summary of all tracked jobs (running + recently finished)."""
        return [
            {
                "job_id": j.job_id,
                "command": j.command[:80],
                "status": "running" if j.is_running else "done",
                "elapsed_seconds": round(j.elapsed_seconds(), 1),
                "exit_code": j.exit_code,
            }
            for j in self._jobs.values()
        ]

    def pop_unnotified_completed(self) -> list[SandboxJob]:
        """
        Return completed jobs that haven't been surfaced in context yet.
        Marks them as notified so they're only returned once.
        """
        ready = [j for j in self._jobs.values() if not j.is_running and not j.notified]
        for j in ready:
            j.notified = True
        return ready

    async def reset(self) -> dict:
        """Wipe /workspace in the sandbox via SSH and clear the job registry."""
        try:
            import asyncssh
            async with asyncssh.connect(**self._get_connect_kwargs()) as conn:
                await conn.run(
                    "rm -rf /workspace/* /workspace/.[^.]*",
                    stdin=asyncssh.DEVNULL,
                    check=False,
                )
            self._jobs.clear()
            logging.info("[sandbox] Workspace wiped, job registry cleared")
            return {"status": "ok", "message": "Sandbox workspace wiped. All jobs cleared."}
        except Exception as e:
            logging.error(f"[sandbox] Reset failed: {e}")
            return {"status": "error", "message": str(e)}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run_job(self, job: SandboxJob) -> None:
        """Execute the command over SSH and stream output into the job buffer."""
        try:
            import asyncssh
            async with asyncssh.connect(**self._get_connect_kwargs()) as conn:
                # Default to /workspace and wrap with a generous shell-level timeout (10 min)
                # as backstop; the fg_timeout in run() handles the UI-facing cutoff.
                wrapped = f"cd /workspace && [ -f /workspace/.sandbox_profile ] && source /workspace/.sandbox_profile; timeout {self._SHELL_TIMEOUT_SECONDS} bash -c {shlex.quote(job.command)}"
                result = await conn.run(
                    wrapped,
                    stdin=asyncssh.DEVNULL,
                    check=False,
                )
                output = (result.stdout or "") + (result.stderr or "")
                job.output.write(output)
                job.exit_code = result.exit_status
        except asyncio.CancelledError:
            job.output.write("\n[killed]")
        except Exception as e:
            job.output.write(f"\n[ssh error: {e}]")
            logging.error(f"[sandbox] Job {job.job_id} SSH error: {e}")
        finally:
            job.finished_at = datetime.now(timezone.utc)


def _truncate(text: str, max_bytes: int) -> str:
    """Truncate output to avoid flooding the AI context."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore") + "\n[... output truncated]"
