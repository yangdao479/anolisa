"""Daemon methods for the secret gateway proxy: status and audit ingest."""

import logging
from typing import Any

from agent_sec_cli.daemon.errors import BadRequestError
from agent_sec_cli.daemon.jobs.secret_gateway import SECRET_GATEWAY_JOB_NAME
from agent_sec_cli.daemon.protocol import DaemonRequest
from agent_sec_cli.daemon.registry import (
    HandlerResult,
    MethodRegistry,
    MethodSpec,
)
from agent_sec_cli.daemon.runtime import DaemonRuntime
from agent_sec_cli.security_events import SecurityEvent, log_event

SECRET_GATEWAY_CATEGORY = "secret_gateway"
SECRET_GATEWAY_EVENT_TYPE = "secret_gateway_inject"

# Only these keys are copied out of an addon report. The addon is a separate
# process with its own release cadence, so pinning the accepted field set keeps
# an unknown or oversized field from reaching the event store.
_AUDIT_FIELDS = (
    "host",
    "method",
    "path",
    "credential_id",
    "carrier",
    "sentinel_matched",
    "injected",
    "status",
    "duration_ms",
    "error",
)
_MAX_STRING_LENGTH = 512

logger = logging.getLogger(__name__)


def _gateway_job(runtime: DaemonRuntime) -> Any | None:
    return runtime.jobs.get(SECRET_GATEWAY_JOB_NAME)


def gateway_status_handler(
    _request: DaemonRequest, runtime: DaemonRuntime
) -> HandlerResult:
    """Return secret gateway proxy runtime state."""
    job = _gateway_job(runtime)
    if job is None:
        # Absent job means the gateway was never enabled; report that plainly
        # instead of failing, so callers can distinguish "off" from "broken".
        return HandlerResult(
            data={
                "enabled": False,
                "state": "disabled",
                "alive": False,
                "pid": None,
            }
        )

    snapshot = job.snapshot()
    snapshot["enabled"] = True
    return HandlerResult(data=snapshot)


def _sanitize_value(key: str, value: Any) -> Any:
    if isinstance(value, str):
        if len(value) > _MAX_STRING_LENGTH:
            return value[:_MAX_STRING_LENGTH] + "...[truncated]"
        return value
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    # Anything else (dict/list/float/object) is not part of the agreed contract.
    raise BadRequestError(f"gateway.audit field {key} has an unsupported type")


def gateway_audit_handler(
    request: DaemonRequest, _runtime: DaemonRuntime
) -> HandlerResult:
    """Persist one gateway decision reported by the mitmproxy addon.

    The addon runs under mitmproxy's bundled interpreter and cannot import this
    package, so it reports over the daemon socket and the daemon owns the write
    into the shared JSONL+SQLite event store. That keeps gateway events in the
    same stream as every other security event while the data plane stays
    replaceable.
    """
    params = request.params
    host = params.get("host")
    if not isinstance(host, str) or not host.strip():
        raise BadRequestError("gateway.audit requires a non-empty host")

    details: dict[str, Any] = {}
    for key in _AUDIT_FIELDS:
        if key in params:
            details[key] = _sanitize_value(key, params[key])

    injected = bool(details.get("injected"))
    # verdict is what `agent-sec-cli events` surfaces for grouping; describe the
    # gateway decision rather than the upstream HTTP outcome.
    details["verdict"] = "inject" if injected else "passthrough"

    failed = details.get("error") is not None
    log_event(
        SecurityEvent(
            event_type=SECRET_GATEWAY_EVENT_TYPE,
            category=SECRET_GATEWAY_CATEGORY,
            details=details,
            result="failed" if failed else "succeeded",
        )
    )
    return HandlerResult(data={"recorded": True})


def register_gateway_methods(registry: MethodRegistry) -> None:
    """Register secret gateway proxy methods."""
    registry.register(
        MethodSpec(
            method="gateway.status",
            handler=gateway_status_handler,
            lifecycle="admin",
            queue="admin",
            timeout_ms=1000,
            access_log=False,
        )
    )
    registry.register(
        MethodSpec(
            method="gateway.audit",
            handler=gateway_audit_handler,
            lifecycle="admin",
            queue="admin",
            timeout_ms=1000,
            # One record per proxied request would double the daemon's access
            # log volume for no added signal; the event store is the record.
            access_log=False,
        )
    )
