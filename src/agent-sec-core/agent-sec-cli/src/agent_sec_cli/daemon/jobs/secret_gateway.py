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
from agent_sec_cli.gateway.ca_trust import (
    CaTrustError,
    SystemTrustStore,
    publish_readable_copy,
)
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

#: mitmdump writes its generated CA here inside the confdir. Only the public
#: certificate; the private key lives in a sibling file we never expose.
MITM_CA_CERT_BASENAME = "mitmproxy-ca-cert.pem"
#: Agent-readable copy of the CA public certificate. The confdir itself stays
#: root-only because it also holds the private key.
DEFAULT_CA_READABLE_PATH = "/opt/agent-sec/gateway/ca-cert.pem"
CA_READABLE_PATH_ENV = "AGENT_SEC_GATEWAY_CA_READABLE_PATH"
#: mitmdump generates the CA a moment after start; poll rather than assume.
CA_WAIT_TIMEOUT_SECONDS = 15.0
CA_WAIT_INTERVAL_SECONDS = 0.25

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
        self._trust_store: SystemTrustStore | None = None
        self._ca_status: str = "pending"
        self._ca_published_path: str | None = None

        self.mitmdump_path = os.environ.get(MITMDUMP_PATH_ENV) or DEFAULT_MITMDUMP_PATH
        self.addon_path = os.environ.get(ADDON_PATH_ENV) or _default_addon_path()
        self.listen_port = self._resolve_port()
        self.mode = os.environ.get(MODE_ENV) or DEFAULT_MODE
        self.confdir = os.environ.get(CONFDIR_ENV) or DEFAULT_CONFDIR
        self.log_path = os.environ.get(LOG_PATH_ENV) or DEFAULT_LOG_PATH
        self.tmpdir = os.environ.get(TMPDIR_ENV) or DEFAULT_TMPDIR
        self.ca_readable_path = (
            os.environ.get(CA_READABLE_PATH_ENV) or DEFAULT_CA_READABLE_PATH
        )

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

        self._deprovision_ca_trust()
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

    # -- CA trust provisioning ---------------------------------------------

    def _mitm_ca_path(self) -> Path:
        """Return the CA public certificate mitmdump generates in its confdir."""
        return Path(self.confdir) / MITM_CA_CERT_BASENAME

    async def _await_ca_file(self) -> Path | None:
        """Wait for mitmdump to generate its CA, up to a bounded timeout.

        mitmdump creates the CA on first start, so this cannot run during
        ``start()``: the file does not exist yet. Polling with a ceiling keeps a
        proxy that fails to initialise from blocking the job forever.
        """
        ca_path = self._mitm_ca_path()
        deadline = asyncio.get_running_loop().time() + CA_WAIT_TIMEOUT_SECONDS
        while True:
            if ca_path.is_file():
                return ca_path
            if asyncio.get_running_loop().time() >= deadline:
                return None
            await asyncio.sleep(CA_WAIT_INTERVAL_SECONDS)

    async def _provision_ca_trust(self) -> None:
        """Publish the CA and install it into the system trust store.

        Runs after the proxy is up because mitmdump owns CA generation. Every
        failure is recorded and surfaced through ``gateway.status`` rather than
        raised: a trust-store problem must not take the data plane down, since
        traffic still flows (clients just have to trust the CA themselves).
        """
        policy = self._policy
        if policy is None:
            return

        if not policy.ca_readable_copy and not policy.install_system_trust:
            self._ca_status = "disabled"
            return

        # _run_once runs again on every proxy restart. Redoing the work is
        # harmless but re-running update-ca-trust on each iteration of a crash
        # loop is not free, so skip when the anchor is verifiably still there.
        if (
            self._ca_status == "installed"
            and self._trust_store is not None
            and self._trust_store.is_installed()
        ):
            return

        ca_path = await self._await_ca_file()
        if ca_path is None:
            self._ca_status = f"failed:CA not generated at {self._mitm_ca_path()}"
            logger.error(
                "secret gateway CA did not appear at %s within %.0fs; "
                "clients will not trust the gateway",
                self._mitm_ca_path(),
                CA_WAIT_TIMEOUT_SECONDS,
            )
            return

        if policy.ca_readable_copy:
            try:
                publish_readable_copy(ca_path, Path(self.ca_readable_path))
                self._ca_published_path = self.ca_readable_path
            except CaTrustError as exc:
                self._ca_published_path = None
                logger.error("secret gateway CA publish failed: %s", exc)

        if not policy.install_system_trust:
            self._ca_status = "disabled"
            logger.info(
                "secret gateway system trust install disabled "
                "(forwarding.install_system_trust=false)"
            )
            return

        store = SystemTrustStore()
        if not store.is_available():
            self._trust_store = None
            self._ca_status = "skipped:no recognised system trust store"
            logger.warning(
                "secret gateway found no system trust store to install the CA "
                "into; clients must trust %s themselves",
                self._ca_published_path or ca_path,
            )
            return

        try:
            outcome = store.install(ca_path)
        except CaTrustError as exc:
            self._trust_store = None
            self._ca_status = f"failed:{exc}"
            logger.error("secret gateway CA trust install failed: %s", exc)
            return

        self._trust_store = store
        self._ca_status = "installed"
        log_daemon_event(
            event="secret_gateway_ca_installed",
            message=(
                f"secret gateway CA installed into system trust store: "
                f"kind={outcome.kind.value} anchor={outcome.anchor_path}"
            ),
            data={
                "kind": outcome.kind.value,
                "anchor_path": str(outcome.anchor_path),
                "readable_path": self._ca_published_path,
            },
        )

    def _deprovision_ca_trust(self) -> None:
        """Remove the CA from the system trust store and drop the public copy.

        Leaving the CA behind would let anything holding the (now orphaned)
        private key impersonate every host the agent talks to, so removal is not
        optional cleanup.
        """
        store = self._trust_store
        if store is not None:
            try:
                if store.uninstall():
                    log_daemon_event(
                        event="secret_gateway_ca_removed",
                        message="secret gateway CA removed from system trust store",
                        data={"anchor_path": str(store.anchor_path())},
                    )
            except CaTrustError as exc:
                logger.error("secret gateway CA trust removal failed: %s", exc)
            finally:
                self._trust_store = None

        if self._ca_published_path is not None:
            try:
                Path(self._ca_published_path).unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning(
                    "could not remove published CA %s: %s",
                    self._ca_published_path,
                    exc,
                )
            self._ca_published_path = None

        self._ca_status = "pending"

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
                    "install_system_trust": policy.install_system_trust,
                    "ca_readable_copy": policy.ca_readable_copy,
                }
            )
        if self._forwarding_error:
            forwarding["error"] = self._forwarding_error
        # Report what the kernel actually has, not what we believe we installed:
        # the gap between the two is exactly the failure mode worth surfacing.
        forwarding["rules_installed"] = (
            self._rules.is_fully_installed() if self._rules is not None else False
        )
        # Same principle for the trust store: report the anchor file that is
        # actually on disk, not merely that we tried to install it.
        forwarding["ca_install_status"] = self._ca_status
        forwarding["ca_trust_installed"] = (
            self._trust_store.is_installed() if self._trust_store is not None else False
        )
        forwarding["ca_readable_path"] = self._ca_published_path

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
                "reinstall sec-core (RPM: /opt/agent-sec/bin/mitmdump ships "
                "with agent-sec-cli; raw: bin/mitmdump next to the wrapper) "
                "or override AGENT_SEC_GATEWAY_MITMDUMP"
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

        # mitmdump generates its CA on first start, so trust provisioning has to
        # happen here rather than in start(). Awaiting it before process.wait()
        # is safe: it has its own bounded timeout.
        await self._provision_ca_trust()

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
