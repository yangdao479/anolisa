"""Forwarding policy: how the agent's traffic is forced through the gateway.

This is the *second* half of the gateway config. ``credentials`` says which
secret to swap on which host; ``forwarding`` says whose traffic to capture and
how. They live in one file so an operator configures the gateway in one place,
but they are parsed separately because they have different consumers: the
credential half is read by the mitmproxy addon (stdlib-only, no project
imports), while this half is read by the daemon that provisions the rules.

Hijack mechanism:
    ``iptables -t nat OUTPUT ... -m owner --uid-owner <agent uid> -j REDIRECT``
    in the root network namespace. The agent needs no cooperation at all: it
    runs as its normal uid and its outbound traffic is redirected. This is fully
    provisioned by starting the daemon.

Kept stdlib-only, matching ``credential_inject_addon.py``, so the parsing rules
can be reasoned about as one unit even though the two files cannot import each
other.
"""

import json
import os
import pwd
import stat
from dataclasses import dataclass, field

DEFAULT_CONFIG_PATH = "/etc/agent-sec/gateway/config.json"
CONFIG_ENV = "AGENT_SEC_GATEWAY_CONFIG"

MODE_UID = "uid"

DEFAULT_LISTEN_PORT = 18080
DEFAULT_PORTS = (80, 443)


class ForwardingConfigError(Exception):
    """Raised when the forwarding section is missing, malformed or unsafe."""


@dataclass(frozen=True)
class ForwardingPolicy:
    """Resolved forwarding policy.

    ``agent_uid`` is always a numeric uid even when the operator wrote a user
    name, because the iptables ``owner`` match works on uids and resolving late
    would mean a rule silently targeting the wrong user after a passwd change.
    """

    mode: str = MODE_UID
    agent_uid: int = 0
    agent_user: str | None = None
    listen_port: int = DEFAULT_LISTEN_PORT
    ports: tuple[int, ...] = DEFAULT_PORTS
    manage_rules: bool = True
    source: str = field(default="", compare=False)

    def describe(self) -> str:
        """One-line summary safe to log (carries no credential material)."""
        who = self.agent_user or f"uid={self.agent_uid}"
        ports = ",".join(str(port) for port in self.ports)
        return (
            f"mode={self.mode} agent={who}({self.agent_uid}) "
            f"redirect={ports}->{self.listen_port} "
            f"manage_rules={self.manage_rules}"
        )


def resolve_config_path() -> str:
    """Return the configured gateway config path."""
    return os.environ.get(CONFIG_ENV, "").strip() or DEFAULT_CONFIG_PATH


def require_secure_config(path: str) -> None:
    """Reject a config file that an agent could read or write.

    The file inlines real credentials, so readability leaks them outright.
    Writability is just as bad in a subtler way: an agent that can append a
    host it controls to ``hosts`` gets the gateway to inject the real token into
    a request aimed at the attacker.

    Mirrors the addon's check on purpose -- the daemon reads this file before
    mitmproxy is even spawned, so it cannot rely on the addon to gate it.

    # Raises
    ForwardingConfigError: when the file is missing, not owned by root, or
    carries any group/other permission bit.
    """
    try:
        info = os.stat(path)
    except FileNotFoundError as exc:
        raise ForwardingConfigError(
            f"gateway config {path} does not exist; copy "
            "scripts/secret-gateway/config.json.example and edit it"
        ) from exc
    except OSError as exc:
        raise ForwardingConfigError(
            f"cannot stat gateway config {path}: {exc}"
        ) from exc

    if info.st_uid != 0:
        raise ForwardingConfigError(
            f"gateway config {path} is owned by uid {info.st_uid}, expected root "
            f"(0). Run: chown root {path}"
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
        raise ForwardingConfigError(
            f"gateway config {path} has mode {stat.S_IMODE(info.st_mode):04o}; "
            f"it inlines real credentials so group/other access must be removed. "
            f"Run: chmod 600 {path}"
        )


def load_forwarding_policy(path: str | None = None) -> ForwardingPolicy:
    """Load and validate the ``forwarding`` section.

    # Raises
    ForwardingConfigError: on unsafe permissions, invalid JSON, or any invalid
    field. Every message names the offending key and the fix, because this file
    is hand-written by an operator.
    """
    config_path = path or resolve_config_path()
    require_secure_config(config_path)

    try:
        with open(config_path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ForwardingConfigError(
            f"gateway config {config_path} is not valid JSON: {exc}"
        ) from exc
    except OSError as exc:
        raise ForwardingConfigError(
            f"cannot read gateway config {config_path}: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise ForwardingConfigError(
            f"gateway config {config_path} must be a JSON object"
        )

    section = raw.get("forwarding")
    if section is None:
        raise ForwardingConfigError(
            f'gateway config {config_path} has no "forwarding" section; add one '
            'with at least {"agent_user": "<the agent\'s user>"}'
        )
    if not isinstance(section, dict):
        raise ForwardingConfigError(
            f'"forwarding" in {config_path} must be a JSON object'
        )

    mode = str(section.get("mode") or MODE_UID).strip().lower()
    if mode != MODE_UID:
        raise ForwardingConfigError(
            f'"forwarding.mode" is "{mode}"; only "uid" is supported'
        )

    agent_uid, agent_user = _resolve_agent(section)
    if agent_uid == 0:
        raise ForwardingConfigError(
            '"forwarding" resolves the agent to uid 0; the agent must not run as '
            "root, otherwise it shares the gateway's trust domain and could read "
            "this config"
        )

    listen_port = _positive_port(section, "listen_port", DEFAULT_LISTEN_PORT)
    ports = _resolve_ports(section)
    if listen_port in ports:
        raise ForwardingConfigError(
            f'"forwarding.listen_port" ({listen_port}) also appears in "ports"; '
            "that would redirect the gateway's own listener onto itself"
        )

    manage_rules = section.get("manage_rules", True)
    if not isinstance(manage_rules, bool):
        raise ForwardingConfigError('"forwarding.manage_rules" must be true or false')

    return ForwardingPolicy(
        mode=mode,
        agent_uid=agent_uid,
        agent_user=agent_user,
        listen_port=listen_port,
        ports=ports,
        manage_rules=manage_rules,
        source=config_path,
    )


def _resolve_agent(section: dict[str, object]) -> tuple[int, str | None]:
    """Resolve the agent to a numeric uid, accepting a uid or a user name."""
    raw_uid = section.get("agent_uid")
    raw_user = section.get("agent_user")

    if raw_uid is None and raw_user is None:
        raise ForwardingConfigError(
            '"forwarding" requires "agent_uid" or "agent_user" so the gateway '
            "knows whose traffic to redirect"
        )
    if raw_uid is not None and raw_user is not None:
        # Two sources of truth for the same fact drift apart silently.
        raise ForwardingConfigError(
            '"forwarding" sets both "agent_uid" and "agent_user"; keep only one'
        )

    if raw_uid is not None:
        if isinstance(raw_uid, bool) or not isinstance(raw_uid, int):
            raise ForwardingConfigError('"forwarding.agent_uid" must be an integer')
        if raw_uid < 0:
            raise ForwardingConfigError('"forwarding.agent_uid" must not be negative')
        return raw_uid, None

    user = str(raw_user).strip()
    if not user:
        raise ForwardingConfigError('"forwarding.agent_user" must not be empty')
    try:
        entry = pwd.getpwnam(user)
    except KeyError as exc:
        raise ForwardingConfigError(
            f'"forwarding.agent_user" is "{user}", which does not exist on this '
            f'host. Create it first, or use "agent_uid" instead'
        ) from exc
    return entry.pw_uid, user


def _positive_port(section: dict[str, object], key: str, default: int) -> int:
    raw = section.get(key)
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ForwardingConfigError(f'"forwarding.{key}" must be an integer')
    if not 1 <= raw <= 65535:
        raise ForwardingConfigError(
            f'"forwarding.{key}" is {raw}; must be between 1 and 65535'
        )
    return raw


def _resolve_ports(section: dict[str, object]) -> tuple[int, ...]:
    raw = section.get("ports")
    if raw is None:
        return DEFAULT_PORTS
    if not isinstance(raw, list) or not raw:
        raise ForwardingConfigError(
            '"forwarding.ports" must be a non-empty list, for example [80, 443]'
        )
    ports: list[int] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ForwardingConfigError(
                f'"forwarding.ports" contains {item!r}; entries must be integers'
            )
        if not 1 <= item <= 65535:
            raise ForwardingConfigError(
                f'"forwarding.ports" contains {item}; must be between 1 and 65535'
            )
        if item not in ports:
            ports.append(item)
    return tuple(ports)
