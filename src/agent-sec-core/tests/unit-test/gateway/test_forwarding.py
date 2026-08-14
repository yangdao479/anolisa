"""Unit tests for forwarding policy parsing and NAT rule provisioning.

No iptables is executed: the rule manager takes an injected runner, so the
argv it would run is asserted directly. That keeps these tests meaningful on a
developer machine while still covering the parts most likely to be wrong --
rule shape, marker handling and the reconcile/teardown bookkeeping.
"""

import json
import os
from collections.abc import Sequence
from pathlib import Path

import pytest
from agent_sec_cli.gateway.forwarding import (
    ForwardingConfigError,
    ForwardingPolicy,
    load_forwarding_policy,
    require_secure_config,
)
from agent_sec_cli.gateway.nat_rules import (
    RULE_MARKER,
    CommandResult,
    NatRuleError,
    NatRuleManager,
)

_CREDENTIAL = {
    "id": "github-token",
    "fake_token": "ghp_fake",
    "real_token": "ghp_real",
    "hosts": ["api.github.com"],
}


def _write_config(tmp_path: Path, forwarding: object) -> str:
    """Write a config whose ownership check will pass for the current user.

    The real check demands root ownership; tests cannot chown, so they assert
    the permission-bit half here and the ownership half is covered separately by
    the mode/uid error paths.
    """
    path = tmp_path / "config.json"
    payload: dict[str, object] = {"credentials": [_CREDENTIAL]}
    if forwarding is not None:
        payload["forwarding"] = forwarding
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    return str(path)


_ROOT_OWNED = pytest.mark.skipif(
    os.geteuid() != 0,
    reason="config ownership check requires the file to be root-owned",
)


# -- policy parsing --------------------------------------------------------


@_ROOT_OWNED
def test_uid_mode_is_the_default(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"agent_uid": 1001})
    policy = load_forwarding_policy(path)

    assert policy.mode == "uid"
    assert policy.agent_uid == 1001
    assert policy.ports == (80, 443)
    assert policy.listen_port == 18080
    assert policy.manage_rules is True
    # CA trust injection is on by default: the operator writing only the config
    # file should get clients that trust the gateway without extra steps.
    assert policy.install_system_trust is True
    assert policy.ca_readable_copy is True


@_ROOT_OWNED
def test_ca_trust_can_be_disabled(tmp_path: Path) -> None:
    """Hosts whose trust store is managed externally must be able to opt out."""
    path = _write_config(
        tmp_path,
        {
            "agent_uid": 1001,
            "install_system_trust": False,
            "ca_readable_copy": False,
        },
    )
    policy = load_forwarding_policy(path)

    assert policy.install_system_trust is False
    assert policy.ca_readable_copy is False


def test_group_readable_config_is_rejected(tmp_path: Path) -> None:
    """The file inlines real credentials, so any group/other bit is fatal.

    Asserted through ``require_secure_config`` directly: on a non-root test host
    the ownership check would fire first and mask the permission-bit check.
    """
    path = Path(_write_config(tmp_path, {"agent_uid": 1001}))
    path.chmod(0o640)

    # When not root, the ownership check trips first -- either message proves the
    # file was refused. When root, we get the precise group/other message.
    expected = (
        "group/other access"
        if os.geteuid() == 0
        else "(group/other access|expected root)"
    )
    with pytest.raises(ForwardingConfigError, match=expected):
        require_secure_config(str(path))


def test_missing_config_names_the_template(tmp_path: Path) -> None:
    with pytest.raises(ForwardingConfigError, match="config.json.example"):
        load_forwarding_policy(str(tmp_path / "absent.json"))


@_ROOT_OWNED
@pytest.mark.parametrize(
    ("forwarding", "match"),
    [
        (None, 'no "forwarding" section'),
        ({}, '"agent_uid" or "agent_user"'),
        ({"agent_uid": 1001, "agent_user": "someone"}, "keep only one"),
        ({"agent_uid": 0}, "must not run as root"),
        ({"agent_uid": -1}, "must not be negative"),
        ({"agent_uid": "1001"}, "must be an integer"),
        ({"agent_uid": 1001, "mode": "netns"}, 'only "uid" is supported'),
        ({"agent_uid": 1001, "ports": []}, "non-empty list"),
        ({"agent_uid": 1001, "ports": [0]}, "between 1 and 65535"),
        ({"agent_uid": 1001, "listen_port": 70000}, "between 1 and 65535"),
        ({"agent_uid": 1001, "manage_rules": "yes"}, "true or false"),
        ({"agent_uid": 1001, "install_system_trust": "yes"}, "true or false"),
        ({"agent_uid": 1001, "ca_readable_copy": 1}, "true or false"),
        ({"agent_uid": 1001, "agent_user": None, "ports": [443, 18080]}, "onto itself"),
    ],
)
def test_invalid_forwarding_is_rejected(
    tmp_path: Path, forwarding: object, match: str
) -> None:
    path = _write_config(tmp_path, forwarding)

    with pytest.raises(ForwardingConfigError, match=match):
        load_forwarding_policy(path)


@_ROOT_OWNED
def test_unknown_agent_user_names_the_alternative(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"agent_user": "no-such-user-xyz"})

    with pytest.raises(ForwardingConfigError, match="agent_uid"):
        load_forwarding_policy(path)


@_ROOT_OWNED
def test_duplicate_ports_are_collapsed(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"agent_uid": 1001, "ports": [443, 443, 80]})

    assert load_forwarding_policy(path).ports == (443, 80)


def test_describe_carries_no_credentials() -> None:
    policy = ForwardingPolicy(agent_uid=1001, agent_user="agentuser")
    described = policy.describe()

    assert "agentuser" in described and "1001" in described
    assert "token" not in described.lower()


# -- rule construction -----------------------------------------------------


class _FakeRunner:
    """Records argv and replays queued results."""

    def __init__(self, results: list[CommandResult] | None = None) -> None:
        self.calls: list[list[str]] = []
        self._results = list(results or [])
        self.default = CommandResult(returncode=0)

    def __call__(self, command: Sequence[str]) -> CommandResult:
        self.calls.append(list(command))
        if self._results:
            return self._results.pop(0)
        return self.default


def _policy(**overrides: object) -> ForwardingPolicy:
    base: dict[str, object] = {"agent_uid": 1001, "ports": (443,)}
    base.update(overrides)
    return ForwardingPolicy(**base)  # type: ignore[arg-type]


def test_uid_mode_rule_shape() -> None:
    manager = NatRuleManager(_policy(), runner=_FakeRunner())
    (rule,) = manager.desired_rules()
    joined = " ".join(rule)

    assert rule[0] == "iptables"
    assert "-t nat -A OUTPUT" in joined
    assert "--uid-owner 1001" in joined
    assert "--dport 443" in joined
    assert "REDIRECT --to-ports 18080" in joined
    assert RULE_MARKER in joined
    assert "netns" not in joined


def test_one_rule_per_port() -> None:
    manager = NatRuleManager(_policy(ports=(80, 443)), runner=_FakeRunner())
    rules = manager.desired_rules()

    assert len(rules) == 2
    assert {r[r.index("--dport") + 1] for r in rules} == {"80", "443"}


def test_marker_encodes_uid_and_ports() -> None:
    manager = NatRuleManager(_policy(), runner=_FakeRunner())

    assert manager.comment_for(443) == f"{RULE_MARKER}:uid=1001:dport=443:to=18080"


# -- reconcile / teardown --------------------------------------------------


def _listing(*comments: str) -> str:
    """Build an ``iptables -S`` style listing, quoting comments like iptables."""
    lines = ["-P OUTPUT ACCEPT", "-A OUTPUT -j SOMETHING_ELSE"]
    for comment in comments:
        lines.append(
            "-A OUTPUT -p tcp -m tcp --dport 443 -m owner --uid-owner 1001 "
            f'-m comment --comment "{comment}" -j REDIRECT --to-ports 18080'
        )
    return "\n".join(lines) + "\n"


def test_reconcile_installs_rules_when_none_exist() -> None:
    runner = _FakeRunner(
        [
            CommandResult(returncode=1),  # preflight probe: rule absent
            CommandResult(returncode=0, stdout=_listing()),  # nothing of ours
            CommandResult(returncode=0),  # install
        ]
    )
    manager = NatRuleManager(_policy(), runner=runner, which=lambda _: "/sbin/iptables")

    assert manager.reconcile() == {"removed": 0, "added": 1}


def test_reconcile_removes_stale_rules_from_a_crashed_daemon() -> None:
    stale = f"{RULE_MARKER}:uid=1001:dport=443:to=9999"
    runner = _FakeRunner(
        [
            CommandResult(returncode=1),  # preflight
            CommandResult(returncode=0, stdout=_listing(stale)),
            CommandResult(returncode=0),  # delete stale
            CommandResult(returncode=0),  # install fresh
        ]
    )
    manager = NatRuleManager(_policy(), runner=runner, which=lambda _: "/sbin/iptables")

    assert manager.reconcile() == {"removed": 1, "added": 1}
    deletes = [c for c in runner.calls if "-D" in c]
    assert len(deletes) == 1
    # The comment must be passed back unquoted, otherwise the delete matches
    # nothing and the stale rule survives.
    assert stale in deletes[0]
    assert f'"{stale}"' not in " ".join(deletes[0])


def test_teardown_ignores_rules_we_do_not_own() -> None:
    runner = _FakeRunner([CommandResult(returncode=0, stdout=_listing())])
    manager = NatRuleManager(_policy(), runner=runner, which=lambda _: "/sbin/iptables")

    assert manager.teardown() == 0
    assert not [c for c in runner.calls if "-D" in c]


def test_failed_install_rolls_back() -> None:
    """A half-installed rule set would redirect some ports and not others."""
    runner = _FakeRunner(
        [
            CommandResult(returncode=1),  # preflight
            CommandResult(returncode=0, stdout=_listing()),
            CommandResult(returncode=0),  # first port installs
            CommandResult(returncode=2, stderr="boom"),  # second fails
            CommandResult(returncode=0, stdout=_listing()),  # rollback listing
        ]
    )
    manager = NatRuleManager(
        _policy(ports=(80, 443)), runner=runner, which=lambda _: "/sbin/iptables"
    )

    with pytest.raises(NatRuleError, match="boom"):
        manager.reconcile()


def test_missing_comment_match_is_fatal() -> None:
    """Without markers we could not tell our rules apart from anyone else's."""
    runner = _FakeRunner(
        [
            CommandResult(
                returncode=2, stderr="iptables: No chain/target/match by that name."
            )
        ]
    )
    manager = NatRuleManager(_policy(), runner=runner, which=lambda _: "/sbin/iptables")

    with pytest.raises(NatRuleError, match="comment"):
        manager.reconcile()


def test_is_fully_installed_requires_every_port() -> None:
    present = f"{RULE_MARKER}:uid=1001:dport=443:to=18080"
    runner = _FakeRunner([CommandResult(returncode=0, stdout=_listing(present))])
    manager = NatRuleManager(
        _policy(ports=(80, 443)), runner=runner, which=lambda _: "/sbin/iptables"
    )

    assert manager.is_fully_installed() is False


def test_is_fully_installed_true_when_all_present() -> None:
    manager = NatRuleManager(_policy(ports=(443,)), runner=_FakeRunner())
    listing = _listing(manager.comment_for(443))
    manager._run = _FakeRunner([CommandResult(returncode=0, stdout=listing)])

    assert manager.is_fully_installed() is True


def test_listing_failure_reports_not_installed() -> None:
    runner = _FakeRunner([CommandResult(returncode=3, stderr="nat table missing")])
    manager = NatRuleManager(_policy(), runner=runner, which=lambda _: "/sbin/iptables")

    assert manager.is_fully_installed() is False
