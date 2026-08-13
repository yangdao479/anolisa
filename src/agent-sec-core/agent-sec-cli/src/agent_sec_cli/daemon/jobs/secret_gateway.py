"""Secret gateway job: supervise the mitmproxy subprocess.

The data plane is a separate process on purpose. mitmproxy ships as a pinned
standalone binary with its own bundled interpreter (see
``scripts/secret-gateway/prepare-mitmproxy.sh``), so it cannot be embedded in this
Python process; running it as a supervised child also keeps a data-plane crash
or OOM from taking the daemon down with it.
"""

import asyncio
import contextlib
import logging
import os
import signal
from pathlib import Path

from agent_sec_cli.daemon.jobs.base import (
    BackgroundJob,
    JobStatus,
    job_trace_context,
    utc_now,
)
from agent_sec_cli.daemon.logging import log_daemon_event
from agent_sec_cli.gateway.forwarding import (
    ForwardingConfigError,
    ForwardingPolicy,
    load_forwarding_policy,
)
from agent_sec_cli.gateway.nat_rules import NatRuleError, NatRuleManager

SECRET_GATEWAY_ENABLED_ENV = "AGENT_SEC_GATEWAY_ENABLED"
SECRET_GATEWAY_JOB_NAME = "secret-gateway"

MITMDUMP_PATH_ENV = "AGENT_SEC_GATEWAY_MITMDUMP"
DEFAULT_MITMDUMP_PATH = "/opt/agent-sec/bin/mitmdump"
ADDON_PATH_ENV = "AGENT_SEC_GATEWAY_ADDON"
LISTEN_PORT_ENV = "AGENT_SEC_GATEWAY_LISTEN_PORT"
DEFAULT_LISTEN_PORT = 18080
MODE_ENV = "AGENT_SEC_GATEWAY_MODE"
DEFAULT_MODE = "transparent"
CONFDIR_ENV = "AGENT_SEC_GATEWAY_CONFDIR"
DEFAULT_CONFDIR = "/etc/agent-sec/gateway/mitm-ca"
LOG_PATH_ENV = "AGENT_SEC_GATEWAY_LOG"
DEFAULT_LOG_PATH = "/var/log/agent-sec/gateway.log"
TMPDIR_ENV = "AGENT_SEC_GATEWAY_TMPDIR"
DEFAULT_TMPDIR = "/var/lib/agent-sec/tmp"
SSL_INSECURE_ENV = "AGENT_SEC_GATEWAY_SSL_INSECURE"

RESTART_BACKOFF_SECONDS = (1.0, 2.0, 5.0, 10.0, 30.0)
TERMINATE_TIMEOUT_SECONDS = 5.0

logger = logging.getLogger(__name__)


def secret_gateway_enabled() -> bool:
    """Return whether the secret gateway proxy job should run.

    Defaults to disabled so that installing this build does not change daemon
    behaviour until an operator opts in.
    """
    return os.environ.get(SECRET_GATEWAY_ENABLED_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _default_addon_path() -> str:
    """Return the packaged addon path.

    Resolved from this module rather than hardcoded so a source checkout and an
    installed package both work. ``parents[2]`` is the ``agent_sec_cli`` package
    root (this file is ``agent_sec_cli/daemon/jobs/secret_gateway.py``).
    """
    return str(
        Path(__file__).resolve().parents[2] / "gateway" / "credential_inject_addon.py"
    )


class SecretGatewayJob(BackgroundJob):
    """Long-running supervisor for the mitmproxy data plane.

    Neither ``OneShotBackgroundJob`` nor ``PeriodicBackgroundJob`` fits: this
    job owns a child process for the daemon's whole lifetime and restarts it
    with backoff, so it implements ``BackgroundJob`` directly.
    """

    name = SECRET_GATEWAY_JOB_NAME

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._stopping = False
        self._state = "stopped"
        self._last_error: str | None = None
        self._last_tick_at: str | None = None
        self._last_started_at: str | None = None
        self._restart_count = 0
        self._policy: ForwardingPolicy | None = None
        self._rules: NatRuleManager | None = None
        self._forwarding_error: str | None = None

        self.mitmdump_path = os.environ.get(MITMDUMP_PATH_ENV) or DEFAULT_MITMDUMP_PATH
        self.addon_path = os.environ.get(ADDON_PATH_ENV) or _default_addon_path()
        self.listen_port = self._resolve_port()
        self.mode = os.environ.get(MODE_ENV) or DEFAULT_MODE
        self.confdir = os.environ.get(CONFDIR_ENV) or DEFAULT_CONFDIR
        self.log_path = os.environ.get(LOG_PATH_ENV) or DEFAULT_LOG_PATH
        self.tmpdir = os.environ.get(TMPDIR_ENV) or DEFAULT_TMPDIR

    @staticmethod
    def _resolve_port() -> int:
        raw = os.environ.get(LISTEN_PORT_ENV, "").strip()
        if not raw:
            return DEFAULT_LISTEN_PORT
        try:
            port = int(raw)
        except ValueError:
            logger.warning(
                "invalid %s=%r; falling back to %d",
                LISTEN_PORT_ENV,
                raw,
                DEFAULT_LISTEN_PORT,
            )
            return DEFAULT_LISTEN_PORT
        if not 1 <= port <= 65535:
            logger.warning(
                "out-of-range %s=%d; falling back to %d",
                LISTEN_PORT_ENV,
                port,
                DEFAULT_LISTEN_PORT,
            )
            return DEFAULT_LISTEN_PORT
        return port

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Provision forwarding, then start supervising the proxy.

        Rules are installed before the child is spawned so that an operator who
        only wrote the config file gets a working gateway from ``daemon start``
        alone. A provisioning failure does *not* abort the daemon: it is recorded
        and surfaced through ``gateway.status``, because a firewall problem on one
        host should not take down the rest of the daemon's duties.
        """
        if self._task is not None and not self._task.done():
            return

        self._stopping = False
        self._provision_forwarding()
        self._state = "running"
        self._task = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        """Stop the supervisor, terminate the child, and remove our rules.

        Rules are removed last and unconditionally: leaving them behind would
        redirect the agent's traffic to a port that no longer has a listener,
        which fails as an unexplained network error rather than a gateway one.
        """
        self._stopping = True

        await self._terminate_process()

        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self._state = "stopped"

        self._deprovision_forwarding()

    # -- forwarding provisioning -------------------------------------------

    def _load_policy(self) -> None:
        """Read the forwarding policy, recording (not raising) any error."""
        try:
            self._policy = load_forwarding_policy()
        except ForwardingConfigError as exc:
            self._policy = None
            self._forwarding_error = str(exc)
            logger.error("secret gateway forwarding config rejected: %s", exc)
            return

        self._forwarding_error = None
        # The config file is the source of truth for the listener port, so the
        # rules and mitmdump cannot disagree about where traffic lands.
        self.listen_port = self._policy.listen_port
        logger.info(
            "secret gateway forwarding policy loaded: %s source=%s",
            self._policy.describe(),
            self._policy.source,
        )

    def _provision_forwarding(self) -> None:
        self._load_policy()
        policy = self._policy
        if policy is None:
            return

        if not policy.manage_rules:
            logger.info(
                "secret gateway rule management disabled "
                "(forwarding.manage_rules=false); install redirect rules yourself"
            )
            self._rules = None
            return

        manager = NatRuleManager(policy)
        try:
            outcome = manager.reconcile()
        except NatRuleError as exc:
            self._rules = None
            self._forwarding_error = str(exc)
            logger.error("secret gateway rule provisioning failed: %s", exc)
            return

        self._rules = manager
        log_daemon_event(
            event="secret_gateway_rules_installed",
            message=(
                f"secret gateway rules installed: "
                f"removed={outcome['removed']} added={outcome['added']} "
                f"mode={policy.mode} agent_uid={policy.agent_uid} "
                f"listen_port={policy.listen_port}"
            ),
            data={
                "removed": outcome["removed"],
                "added": outcome["added"],
                "mode": policy.mode,
                "agent_uid": policy.agent_uid,
                "listen_port": policy.listen_port,
            },
        )

    def _deprovision_forwarding(self) -> None:
        manager = self._rules
        if manager is None:
            return
        try:
            removed = manager.teardown()
        except NatRuleError as exc:
            logger.error("secret gateway rule teardown failed: %s", exc)
            return
        finally:
            self._rules = None
        if removed:
            log_daemon_event(
                event="secret_gateway_rules_removed",
                message=f"secret gateway rules removed: count={removed}",
                data={"removed": removed},
            )

    def status(self) -> JobStatus:
        """Return the JobManager-facing status."""
        return JobStatus(
            name=self.name,
            state=self._state,
            last_error=self._last_error or self._forwarding_error,
            last_tick_at=self._last_tick_at,
            last_started_at=self._last_started_at,
        )

    def snapshot(self) -> dict[str, object]:
        """Return proxy runtime details for the ``gateway.status`` method.

        Carries no credential material -- only process, forwarding and
        configuration identity that is safe to expose to an unprivileged caller.
        """
        process = self._process
        pid = (
            process.pid if process is not None and process.returncode is None else None
        )
        policy = self._policy

        forwarding: dict[str, object] = {"configured": policy is not None}
        if policy is not None:
            forwarding.update(
                {
                    "mode": policy.mode,
                    "agent_uid": policy.agent_uid,
                    "agent_user": policy.agent_user,
                    "redirect_ports": list(policy.ports),
                    "manage_rules": policy.manage_rules,
                }
            )
        if self._forwarding_error:
            forwarding["error"] = self._forwarding_error
        # Report what the kernel actually has, not what we believe we installed:
        # the gap between the two is exactly the failure mode worth surfacing.
        forwarding["rules_installed"] = (
            self._rules.is_fully_installed() if self._rules is not None else False
        )

        return {
            "state": self._state,
            "pid": pid,
            "alive": pid is not None,
            "listen_port": self.listen_port,
            "mode": self.mode,
            "restart_count": self._restart_count,
            "mitmdump_path": self.mitmdump_path,
            "addon_path": self.addon_path,
            "log_path": self.log_path,
            "forwarding": forwarding,
            "last_error": self._last_error,
            "last_started_at": self._last_started_at,
        }

    # -- supervision -------------------------------------------------------

    def _build_command(self) -> list[str]:
        command: list[str] = [
            self.mitmdump_path,
            "--mode",
            self.mode,
            "--listen-host",
            "0.0.0.0",
            "--listen-port",
            str(self.listen_port),
            "-s",
            self.addon_path,
            "--set",
            f"confdir={self.confdir}",
            # The agent's traffic is what we intercept; blocking "global"
            # (non-private) clients would reject it in transparent mode.
            "--set",
            "block_global=false",
            # Silence mitmproxy's own flow dumper. It echoes the full request
            # URL, which for a query-carried credential contains the real token
            # after injection -- that would put the secret in proxy.log. Our
            # addon emits a scrubbed structured record instead.
            "--set",
            "flow_detail=0",
        ]
        if os.environ.get(SSL_INSECURE_ENV, "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            # Needed only for test upstreams that use a self-signed cert.
            command += ["--set", "ssl_insecure=true"]
        return command

    def _child_env(self) -> dict[str, str]:
        env = dict(os.environ)
        # PyInstaller single-file builds unpack into TMPDIR on each start, so a
        # noexec /tmp would make the binary refuse to run.
        env["TMPDIR"] = self.tmpdir
        return env

    async def _supervise(self) -> None:
        attempt = 0
        while not self._stopping:
            with job_trace_context(self.name) as trace_context:
                started_at = utc_now()
                self._last_started_at = started_at
                self._last_tick_at = started_at

                try:
                    returncode = await self._run_once(trace_context)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._state = "error"
                    self._last_error = str(exc)
                    log_daemon_event(
                        level=logging.ERROR,
                        event="secret_gateway_spawn_failed",
                        message="secret gateway proxy failed to start",
                        data={
                            "job_name": self.name,
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                            "mitmdump_path": self.mitmdump_path,
                        },
                        trace_context=trace_context,
                    )
                else:
                    if self._stopping:
                        break
                    self._state = "error"
                    self._last_error = f"proxy exited with code {returncode}"
                    self._restart_count += 1
                    log_daemon_event(
                        level=logging.ERROR,
                        event="secret_gateway_exited",
                        message="secret gateway proxy exited unexpectedly",
                        data={
                            "job_name": self.name,
                            "returncode": returncode,
                            "restart_count": self._restart_count,
                        },
                        trace_context=trace_context,
                    )

            if self._stopping:
                break

            delay = RESTART_BACKOFF_SECONDS[
                min(attempt, len(RESTART_BACKOFF_SECONDS) - 1)
            ]
            attempt += 1
            await asyncio.sleep(delay)

        self._state = "stopped"

    async def _run_once(self, trace_context: object) -> int:
        """Spawn the proxy and wait for it to exit. Returns its exit code.

        # Errors
        Raises ``FileNotFoundError`` when the pinned mitmdump binary is absent,
        which is a configuration error rather than a transient failure.
        """
        if not os.path.exists(self.mitmdump_path):
            raise FileNotFoundError(
                f"mitmdump not found at {self.mitmdump_path}; "
                "run scripts/secret-gateway/prepare-mitmproxy.sh"
            )

        os.makedirs(self.tmpdir, exist_ok=True)
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

        command = self._build_command()
        # mitmproxy's own stdout/stderr goes to the same demo log as the
        # addon's records so one file tells the whole story.
        log_handle = open(self.log_path, "a", encoding="utf-8")  # noqa: SIM115
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=log_handle,
                stderr=log_handle,
                env=self._child_env(),
                start_new_session=True,
            )
        finally:
            log_handle.close()

        self._process = process
        self._state = "running"
        self._last_error = None
        log_daemon_event(
            event="secret_gateway_started",
            message="secret gateway proxy started",
            data={
                "job_name": self.name,
                "pid": process.pid,
                "listen_port": self.listen_port,
                "mode": self.mode,
            },
            trace_context=trace_context,
        )

        returncode = await process.wait()
        self._process = None
        return returncode

    async def _terminate_process(self) -> None:
        process = self._process
        if process is None or process.returncode is not None:
            return

        # start_new_session=True put the child in its own process group, so
        # signal the group to also reap anything mitmproxy spawned.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)

        try:
            await asyncio.wait_for(process.wait(), timeout=TERMINATE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    process.wait(), timeout=TERMINATE_TIMEOUT_SECONDS
                )

        self._process = None
