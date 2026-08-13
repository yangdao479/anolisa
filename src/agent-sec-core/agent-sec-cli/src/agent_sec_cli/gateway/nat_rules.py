"""NAT rule provisioning for the secret gateway.

Owns the one thing that makes "write the config, start the daemon" work: the
``iptables`` rules that force the agent's outbound traffic into the gateway.

Every rule this module creates carries a comment marker. That marker is what
makes the whole thing safe to automate:

* it lets startup **reconcile** -- delete rules a previous daemon left behind
  after a crash before installing fresh ones. Stale rules are worse than no
  rules: they redirect the agent to a port nobody listens on, so traffic fails
  in a way that looks like a network fault rather than a gateway fault;
* it scopes deletion to *our* rules, so teardown can never remove a rule some
  other system installed.

Because the marker carries that much weight, a host without the ``comment``
match is rejected rather than silently falling back to unmarked rules.

Command execution is injected so the rule set can be asserted in tests without
touching the host firewall.
"""

import logging
import shlex
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from agent_sec_cli.gateway.forwarding import ForwardingPolicy

logger = logging.getLogger(__name__)

#: Marker prefix on every rule we own. Never change without a migration: an
#: upgraded daemon must still recognise the previous version's rules to clean
#: them up.
RULE_MARKER = "agent-sec-gateway"

NAT_TABLE = "nat"
OUTPUT_CHAIN = "OUTPUT"


class NatRuleError(Exception):
    """Raised when rules cannot be inspected or applied."""


CommandRunner = Callable[[Sequence[str]], "CommandResult"]


@dataclass(frozen=True)
class CommandResult:
    """Outcome of one command invocation."""

    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_command(command: Sequence[str]) -> CommandResult:
    """Execute *command*, capturing output. Never raises on non-zero exit."""
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        list(command),
        capture_output=True,
        text=True,
        check=False,
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


class NatRuleManager:
    """Install, reconcile and remove the gateway's redirect rules."""

    def __init__(
        self,
        policy: ForwardingPolicy,
        runner: CommandRunner | None = None,
        iptables: str = "iptables",
        which: Callable[[str], str | None] | None = None,
    ) -> None:
        self.policy = policy
        self._run = runner or run_command
        self._iptables = iptables
        # Injectable so the rule set can be asserted on a host that has no
        # iptables at all; production always uses shutil.which.
        self._which = which or shutil.which

    # -- command construction ---------------------------------------------

    def _prefix(self) -> list[str]:
        """Return the iptables argv prefix."""
        return [self._iptables]

    def comment_for(self, port: int) -> str:
        """Return the marker for one redirected port.

        Encodes uid and target port so a policy change produces a different
        marker, which makes a stale rule from an older policy visibly distinct.
        """
        return (
            f"{RULE_MARKER}:uid={self.policy.agent_uid}"
            f":dport={port}:to={self.policy.listen_port}"
        )

    def desired_rules(self) -> list[list[str]]:
        """Return the full argv for each rule that should exist."""
        rules = []
        for port in self.policy.ports:
            rules.append(
                [
                    *self._prefix(),
                    "-t",
                    NAT_TABLE,
                    "-A",
                    OUTPUT_CHAIN,
                    "-p",
                    "tcp",
                    "--dport",
                    str(port),
                    "-m",
                    "owner",
                    "--uid-owner",
                    str(self.policy.agent_uid),
                    "-m",
                    "comment",
                    "--comment",
                    self.comment_for(port),
                    "-j",
                    "REDIRECT",
                    "--to-ports",
                    str(self.policy.listen_port),
                ]
            )
        return rules

    # -- preflight ---------------------------------------------------------

    def preflight(self) -> None:
        """Verify the host can support marked rules.

        # Raises
        NatRuleError: when iptables is missing or the comment match is
        unavailable, since unmarked rules could not be cleaned up safely.
        """
        if self._which(self._iptables) is None:
            raise NatRuleError(
                f"{self._iptables} not found; the gateway needs it to redirect the "
                "agent's traffic. Install iptables or set forwarding.manage_rules "
                "to false and install the rules yourself"
            )

        probe = self._run(
            [
                *self._prefix(),
                "-t",
                NAT_TABLE,
                "-C",
                OUTPUT_CHAIN,
                "-m",
                "comment",
                "--comment",
                f"{RULE_MARKER}:probe",
                "-j",
                "ACCEPT",
            ]
        )
        # Rule-absent (exit 1) is the expected answer; what we are ruling out is
        # "no such match" / "unknown option", which indicates a missing module.
        combined = f"{probe.stdout}\n{probe.stderr}".lower()
        if "no chain/target/match" in combined or "unknown option" in combined:
            raise NatRuleError(
                "this host's iptables lacks the 'comment' match, which the gateway "
                "requires to tag and later clean up its own rules. Load the "
                "xt_comment module, or set forwarding.manage_rules to false"
            )

    # -- inspection --------------------------------------------------------

    def installed_rules(self) -> list[str]:
        """Return the ``-A`` specs of existing rules bearing our marker."""
        result = self._run([*self._prefix(), "-t", NAT_TABLE, "-S", OUTPUT_CHAIN])
        if not result.ok:
            raise NatRuleError(
                f"cannot list {NAT_TABLE}/{OUTPUT_CHAIN} rules: "
                f"{result.stderr.strip() or result.returncode}"
            )
        return [
            line.strip()
            for line in result.stdout.splitlines()
            if RULE_MARKER in line and line.strip().startswith("-A")
        ]

    def is_fully_installed(self) -> bool:
        """Whether every desired rule is currently present.

        Used by status reporting so an operator can tell "the gateway thinks it
        installed rules" apart from "the rules are actually in the kernel".
        """
        try:
            installed = "\n".join(self.installed_rules())
        except NatRuleError:
            return False
        return all(self.comment_for(port) in installed for port in self.policy.ports)

    # -- mutation ----------------------------------------------------------

    def reconcile(self) -> dict[str, int]:
        """Remove our stale rules, then install the desired set.

        Idempotent: running it twice leaves the same rules. Returns counts for
        logging so a restart that cleaned up residue is visible.
        """
        self.preflight()
        removed = self._remove_marked()

        added = 0
        for rule in self.desired_rules():
            result = self._run(rule)
            if not result.ok:
                # Roll back rather than leave a half-installed rule set, which
                # would redirect some ports and not others.
                self._remove_marked()
                raise NatRuleError(
                    "failed to install gateway redirect rule "
                    f"({' '.join(rule)}): "
                    f"{result.stderr.strip() or result.returncode}"
                )
            added += 1

        logger.info(
            "secret gateway nat rules reconciled: removed=%d added=%d policy=%s",
            removed,
            added,
            self.policy.describe(),
        )
        return {"removed": removed, "added": added}

    def teardown(self) -> int:
        """Remove every rule bearing our marker. Returns how many were removed."""
        removed = self._remove_marked()
        if removed:
            logger.info("secret gateway nat rules removed: count=%d", removed)
        return removed

    def _remove_marked(self) -> int:
        """Delete all marked rules by converting their ``-A`` spec to ``-D``."""
        try:
            specs = self.installed_rules()
        except NatRuleError:
            # Listing can fail on a host where the nat table is unavailable;
            # there is then nothing of ours to remove either.
            return 0

        removed = 0
        for spec in specs:
            # shlex, not str.split: iptables -S quotes the comment value, and
            # feeding those quotes back as part of the comment makes the delete
            # silently match nothing.
            try:
                argv = shlex.split(spec)
            except ValueError:
                logger.warning("could not parse gateway rule spec %r", spec)
                continue
            if not argv or argv[0] != "-A":
                continue
            argv[0] = "-D"
            result = self._run([*self._prefix(), "-t", NAT_TABLE, *argv])
            if result.ok:
                removed += 1
            else:
                logger.warning(
                    "could not remove gateway rule %r: %s",
                    spec,
                    result.stderr.strip() or result.returncode,
                )
        return removed
