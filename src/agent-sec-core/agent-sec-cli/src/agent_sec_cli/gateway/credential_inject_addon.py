"""mitmproxy addon: replace placeholder credentials with real ones on egress.

Provider-agnostic by construction: every decision comes from config -- which
hosts a credential is scoped to, which header carries it, and the fake/real pair
to swap. Adding GitHub, OpenAI, Anthropic, Azure or an internal service is a
config edit, not a code change. Nothing here may grow per-provider branches; if
some provider needs different *mechanics* (for example a credential carried in a
query parameter rather than a header), extend the config schema and keep the
matching generic.

Loaded by the *bundled* interpreter of the pinned mitmproxy standalone binary,
so this module must import nothing beyond the standard library and the
``mitmproxy`` package itself. Anything that needs project logic is handed to
agent-sec-daemon over its NDJSON Unix socket (see ``_DaemonAudit``).

Threat model in one line: the agent only ever holds a same-shaped placeholder,
and the real credential lives in a root-owned config only this process reads, so
a compromised agent has nothing to exfiltrate.

Usage::

    mitmdump --mode transparent --listen-port 18080 \
        -s .../credential_inject_addon.py --set block_global=false
"""

import hashlib
import json
import logging
import os
import socket
import stat
import time
from typing import Any

from mitmproxy import http

CONFIG_ENV = "AGENT_SEC_GATEWAY_CONFIG"
DEFAULT_CONFIG_PATH = "/etc/agent-sec/gateway/config.json"
DEFAULT_LOG_PATH = "/var/log/agent-sec/gateway.log"
DEFAULT_AUDIT_TIMEOUT_MS = 800

LOCATION_HEADER = "header"
LOCATION_QUERY = "query"
_LOCATIONS = (LOCATION_HEADER, LOCATION_QUERY)

# Flow-scoped keys. mitmproxy keeps ``metadata`` across hooks for one flow,
# which is how the request hook tells the response hook what it did.
_META_CREDENTIAL = "agent_sec_credential_id"
_META_INJECTED = "agent_sec_injected"
_META_MATCHED = "agent_sec_sentinel_matched"
_META_CARRIER = "agent_sec_carrier"

logger = logging.getLogger("agent_sec.gateway")


def _digest(secret: str) -> str:
    """Return a short, non-reversible fingerprint for log correlation."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]


def _mask(secret: str) -> str:
    """Return a masked form that keeps only the credential's shape."""
    if not secret:
        return ""
    prefix = secret[:4] if len(secret) > 8 else secret[:1]
    return f"{prefix}***[len={len(secret)},sha256={_digest(secret)}]"


def _require_not_agent_writable(path: str, label: str) -> None:
    """Refuse a path the unprivileged agent could read or tamper with.

    The config file carries the real credential inline *and* decides which
    hosts receive it, so it is doubly load-bearing:

    * readable by the agent -> the agent simply reads the credential;
    * writable by the agent -> the agent appends its own host to ``hosts`` and
      has the gateway inject the real token into a request aimed at a server it
      controls.

    So it must be root-owned with no group/other access. Checked at startup and
    fatal: a security component that cannot prove its own inputs are protected
    must not start.

    # Errors
    Raises ``PermissionError`` when the owner is not root or when any
    group/other permission bit is set.
    """
    info = os.stat(path)
    if info.st_uid != 0:
        raise PermissionError(
            f"{label} {path} is owned by uid {info.st_uid}, expected root (0). "
            "Run: chown root " + path
        )

    leaked = stat.S_IMODE(info.st_mode) & (
        stat.S_IRGRP
        | stat.S_IWGRP
        | stat.S_IXGRP
        | stat.S_IROTH
        | stat.S_IWOTH
        | stat.S_IXOTH
    )
    if leaked:
        raise PermissionError(
            f"{label} {path} has mode {stat.S_IMODE(info.st_mode):04o}; "
            "group/other access must be removed so the agent cannot read or "
            f"tamper with it. Run: chmod 600 {path}"
        )


class _Credential:
    """One managed credential and the hosts it may be injected into."""

    def __init__(self, raw: dict[str, Any], index: int) -> None:
        # Errors name the array index as well as the id, because a hand-edited
        # config may not have a usable id yet.
        where = f"credentials[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{where} must be a JSON object")

        self.id = str(raw.get("id") or "").strip()
        if not self.id:
            raise ValueError(f'{where} requires a non-empty "id"')

        self.fake_token = str(raw.get("fake_token") or "").strip()
        if not self.fake_token:
            raise ValueError(f'{where} ("{self.id}") requires "fake_token"')

        # The real credential lives inline in the config. That is why the config
        # file is treated as a secret and its permissions are enforced at
        # startup -- see _require_not_agent_writable.
        self.real_token = str(raw.get("real_token") or "").strip()
        if not self.real_token:
            raise ValueError(f'{where} ("{self.id}") requires "real_token"')

        if self.real_token == self.fake_token:
            raise ValueError(
                f'{where} ("{self.id}"): "real_token" and "fake_token" are '
                "identical, so injection would be a no-op"
            )

        hosts = raw.get("hosts")
        if not isinstance(hosts, list) or not hosts:
            raise ValueError(
                f'{where} ("{self.id}") requires "hosts" as a non-empty list, '
                'for example ["api.example.com"]'
            )
        # Host matching is case-insensitive because SNI/Host casing varies.
        self.hosts = frozenset(str(host).strip().lower() for host in hosts)
        if "" in self.hosts:
            raise ValueError(f'{where} ("{self.id}") has an empty entry in "hosts"')

        # Where the credential rides: a request header (the common case) or a
        # query parameter (Gitee's access_token, Google's key, ...).
        self.location = str(raw.get("location") or LOCATION_HEADER).strip().lower()
        if self.location not in _LOCATIONS:
            raise ValueError(
                f'{where} ("{self.id}") has unsupported "location" '
                f'"{self.location}"; expected one of {", ".join(_LOCATIONS)}'
            )

        # Reject contradictory config rather than silently ignoring a field the
        # operator clearly meant to take effect.
        if self.location == LOCATION_QUERY and "header" in raw:
            raise ValueError(
                f'{where} ("{self.id}") sets "header" but location is '
                '"query"; use "param" instead'
            )
        if self.location == LOCATION_HEADER and "param" in raw:
            raise ValueError(
                f'{where} ("{self.id}") sets "param" but location is '
                '"header"; use "header" instead'
            )

        self.header = str(raw.get("header") or "Authorization")
        self.param = str(raw.get("param") or "").strip()
        if self.location == LOCATION_QUERY and not self.param:
            raise ValueError(
                f'{where} ("{self.id}") requires "param" when location is '
                '"query", for example "access_token"'
            )

    def carrier(self) -> str:
        """Return a human-readable description of where the credential rides."""
        if self.location == LOCATION_QUERY:
            return f"query:{self.param}"
        return f"header:{self.header}"

    def matches_host(self, host: str) -> bool:
        """Return whether this credential is scoped to *host*."""
        return host.lower() in self.hosts


class _DaemonAudit:
    """Best-effort NDJSON client for the daemon's ``gateway.audit`` method.

    Auditing must never affect traffic: every failure degrades to the local
    log. The daemon owns the real audit write so that proxy events land in the
    same JSONL+SQLite store as every other security event.
    """

    def __init__(self, socket_path: str, timeout_ms: int) -> None:
        self.socket_path = socket_path
        self.timeout_seconds = max(0.05, timeout_ms / 1000)
        self._degraded_logged = False

    def send(self, details: dict[str, Any]) -> bool:
        """Report one gateway decision. Returns whether the daemon accepted it."""
        if not self.socket_path:
            return False

        payload = {
            "method": "gateway.audit",
            "params": details,
            "trace_context": {},
            "caller": "secret-gateway-addon",
            "timeout_ms": int(self.timeout_seconds * 1000),
        }
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout_seconds)
                sock.connect(self.socket_path)
                sock.sendall(line.encode("utf-8"))
                # The trailing newline already delimits the frame, so the
                # daemon answers without needing a half-close.
                raw = self._read_line(sock)
        except OSError as exc:
            self._note_degraded(str(exc))
            return False

        if not raw:
            self._note_degraded("daemon closed the connection without a response")
            return False

        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._note_degraded(f"malformed daemon response: {exc}")
            return False

        if not response.get("ok"):
            error = response.get("error") or {}
            self._note_degraded(f"daemon rejected audit: {error.get('code')}")
            return False

        self._degraded_logged = False
        return True

    @staticmethod
    def _read_line(sock: socket.socket) -> bytes:
        chunks = bytearray()
        while b"\n" not in chunks:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.extend(chunk)
            # The daemon caps responses; stop early rather than buffer forever.
            if len(chunks) > 1024 * 1024:
                break
        return bytes(chunks)

    def _note_degraded(self, reason: str) -> None:
        # Log the transition once so a down daemon cannot flood the proxy log.
        if not self._degraded_logged:
            logger.warning(
                "secret gateway audit degraded to local log only: %s", reason
            )
            self._degraded_logged = True


class CredentialInjector:
    """Swap fake credentials for real ones on requests to scoped hosts."""

    def __init__(self) -> None:
        self.credentials: list[_Credential] = []
        self.audit: _DaemonAudit | None = None
        self.log_path = DEFAULT_LOG_PATH
        self.requests_total = 0
        self.injections_total = 0

    # -- lifecycle ---------------------------------------------------------

    def running(self) -> None:
        """Load configuration and credentials once the proxy is up.

        The config is hand-written by an operator, so every failure here names
        the file, the offending field and the fix. Failures are fatal by
        design: mitmproxy aborts startup rather than serving traffic with no
        injection configured, which would forward the fake token upstream and
        look like a broken credential instead of a broken gateway.
        """
        config_path = os.environ.get(CONFIG_ENV, DEFAULT_CONFIG_PATH)
        if not os.path.exists(config_path):
            raise FileNotFoundError(
                f"secret gateway config {config_path} does not exist "
                f"(override the path with {CONFIG_ENV})"
            )

        # The config decides which hosts receive the real credential AND holds
        # the credential itself, so an agent able to read it gets the token and
        # an agent able to write it can redirect injection to a host it
        # controls. Both are fatal; check before parsing.
        _require_not_agent_writable(config_path, "secret gateway config")

        try:
            with open(config_path, encoding="utf-8") as handle:
                config = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"secret gateway config {config_path} is not valid JSON: "
                f"{exc.msg} (line {exc.lineno}, column {exc.colno})"
            ) from exc

        if not isinstance(config, dict):
            raise ValueError(f"{config_path} must contain a JSON object")

        self.log_path = str(config.get("log_path") or DEFAULT_LOG_PATH)

        entries = config.get("credentials")
        if not isinstance(entries, list) or not entries:
            raise ValueError(f'{config_path} requires a non-empty "credentials" array')

        credentials = []
        seen_fake_tokens: dict[str, str] = {}
        for index, entry in enumerate(entries):
            credential = _Credential(entry, index)
            # A fake token shared by two credentials makes injection
            # order-dependent and therefore unpredictable; reject it outright.
            previous = seen_fake_tokens.get(credential.fake_token)
            if previous is not None:
                raise ValueError(
                    f'{config_path}: credentials "{previous}" and '
                    f'"{credential.id}" share the same fake_token'
                )
            seen_fake_tokens[credential.fake_token] = credential.id
            credentials.append(credential)
        self.credentials = credentials

        socket_path = str(config.get("daemon_socket") or "")
        timeout_ms = int(config.get("audit_timeout_ms") or DEFAULT_AUDIT_TIMEOUT_MS)
        self.audit = _DaemonAudit(socket_path, timeout_ms)

        self._configure_file_log()
        logger.info(
            "secret gateway config loaded: path=%s credentials=%d audit=%s",
            config_path,
            len(self.credentials),
            socket_path or "local-log-only",
        )
        for credential in self.credentials:
            logger.info(
                "secret gateway credential ready: id=%s carrier=%s hosts=%s "
                "fake=%s real=%s",
                credential.id,
                credential.carrier(),
                ",".join(sorted(credential.hosts)),
                _mask(credential.fake_token),
                _mask(credential.real_token),
            )

    def _configure_file_log(self) -> None:
        """Mirror addon logs into the demo log file."""
        try:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            handler = logging.FileHandler(self.log_path, encoding="utf-8")
        except OSError as exc:
            logger.warning("secret gateway log file unavailable: %s", exc)
            return

        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        # mitmproxy owns the root logger and the job points mitmdump's stdout at
        # this same file, so propagating would write every record twice.
        logger.propagate = False

    # -- traffic hooks -----------------------------------------------------

    def request(self, flow: http.HTTPFlow) -> None:
        """Inject the real credential when host and placeholder both match."""
        self.requests_total += 1
        host = flow.request.pretty_host

        for credential in self.credentials:
            if not credential.matches_host(host):
                continue
            if not self._inject(flow, credential):
                continue

            self.injections_total += 1
            flow.metadata[_META_CREDENTIAL] = credential.id
            flow.metadata[_META_CARRIER] = credential.carrier()
            flow.metadata[_META_MATCHED] = True
            flow.metadata[_META_INJECTED] = True
            return

        flow.metadata[_META_MATCHED] = False
        flow.metadata[_META_INJECTED] = False

    @staticmethod
    def _inject(flow: http.HTTPFlow, credential: "_Credential") -> bool:
        """Swap placeholder for real credential. Returns whether it matched.

        Both carriers replace only the token *substring*, so whatever surrounds
        it survives: the caller's scheme keyword for a header (``Bearer <t>``
        and ``token <t>`` both work with one rule), and any other query
        parameters for a URL.
        """
        if credential.location == LOCATION_QUERY:
            values = flow.request.query.get_all(credential.param)
            if not any(credential.fake_token in value for value in values):
                return False
            # set_all covers a parameter repeated in the URL, and assigning
            # through the view rewrites Request.path with correct encoding.
            flow.request.query.set_all(
                credential.param,
                [
                    value.replace(credential.fake_token, credential.real_token)
                    for value in values
                ],
            )
            return True

        header_value = flow.request.headers.get(credential.header)
        if not header_value or credential.fake_token not in header_value:
            return False
        flow.request.headers[credential.header] = header_value.replace(
            credential.fake_token, credential.real_token
        )
        return True

    def response(self, flow: http.HTTPFlow) -> None:
        """Log and audit one completed flow."""
        status = flow.response.status_code if flow.response else None
        self._record(flow, status=status, error=None)

    def error(self, flow: http.HTTPFlow) -> None:
        """Log and audit a flow that failed before completing."""
        reason = str(flow.error) if flow.error else "unknown error"
        self._record(flow, status=None, error=reason)

    # -- reporting ---------------------------------------------------------

    def _record(
        self,
        flow: http.HTTPFlow,
        status: int | None,
        error: str | None,
    ) -> None:
        credential_id = flow.metadata.get(_META_CREDENTIAL)
        injected = bool(flow.metadata.get(_META_INJECTED))
        matched = bool(flow.metadata.get(_META_MATCHED))
        carrier = flow.metadata.get(_META_CARRIER)

        details: dict[str, Any] = {
            "host": flow.request.pretty_host,
            "method": flow.request.method,
            # Scrubbed: for a query-carried credential the path holds the real
            # token by the time this runs.
            "path": self._scrub(flow.request.path),
            "credential_id": credential_id,
            "carrier": carrier,
            "sentinel_matched": matched,
            "injected": injected,
            "status": status,
            "duration_ms": self._duration_ms(flow),
        }
        if error is not None:
            details["error"] = self._scrub(error)

        accepted = False
        if self.audit is not None:
            accepted = self.audit.send(dict(details))
        details["audited"] = accepted

        # The demo log is the always-available record; never put a token in it.
        logger.info("secret_gateway_flow %s", json.dumps(details, ensure_ascii=False))

    def _scrub(self, text: str) -> str:
        """Redact any real credential that appears in *text*.

        Needed because a query-carried credential ends up inside the request
        path once injected, and the path is both logged and audited. Header
        values are never logged, so this is the only place a real credential can
        reach an output.
        """
        for credential in self.credentials:
            if credential.real_token and credential.real_token in text:
                text = text.replace(
                    credential.real_token, f"<{credential.id}:redacted>"
                )
        return text

    @staticmethod
    def _duration_ms(flow: http.HTTPFlow) -> int | None:
        started = flow.request.timestamp_start if flow.request else None
        if started is None:
            return None
        finished = flow.response.timestamp_end if flow.response else None
        if finished is None:
            finished = time.time()
        return max(0, int((finished - started) * 1000))


addons = [CredentialInjector()]
