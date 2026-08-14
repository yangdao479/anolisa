"""System trust store injection for the secret gateway CA.

Data-plane clients that read the OS trust store (curl, wget, Go net/http, most
Rust HTTPS libraries) can be made to accept the gateway's self-signed CA by
dropping a single ``.pem`` file into the distro's anchor directory and running
the associated update command. Doing it here means the operator only writes the
gateway config and starts the daemon; no manual ``cp`` + ``update-ca-trust``
step, no per-agent recipe.

Two limits to keep in mind:

* Applications that carry their own CA bundle (Python ``certifi``, Node.js,
  Java keystore) do **not** consult the system store. Those still need env
  vars or code changes on the caller side; this module cannot help them.
* Distributions that use neither ca-trust nor ca-certificates (Alpine's
  ``apk`` layout, containers without update-ca-* commands) are skipped rather
  than failed -- daemon start continues, and the status object records why the
  install was skipped so the operator can see it.

Command execution and ``shutil.which`` are injectable so callers can assert the
rollout without touching the host trust store, matching the pattern in
``nat_rules.py``.
"""

import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from agent_sec_cli.gateway.nat_rules import (
    CommandRunner,
    run_command,
)

logger = logging.getLogger(__name__)

#: File name every install we own carries. Uses ``agent-sec-gateway`` as the
#: prefix so ``ls /etc/pki/ca-trust/source/anchors/ | grep agent-sec-gateway``
#: is enough to identify our anchors on any host. Do not rename without a
#: migration -- an older daemon must still recognise the previous file to
#: clean it up.
_ANCHOR_BASENAME_RPM = "agent-sec-gateway.pem"
_ANCHOR_BASENAME_DEBIAN = "agent-sec-gateway.crt"

_RPM_ANCHOR_DIR = Path("/etc/pki/ca-trust/source/anchors")
_RPM_UPDATE_CMD = "update-ca-trust"

_DEBIAN_ANCHOR_DIR = Path("/usr/local/share/ca-certificates")
_DEBIAN_UPDATE_CMD = "update-ca-certificates"


class TrustStoreKind(str, Enum):
    """Which flavour of system trust store the host uses."""

    RPM = "rpm"
    DEBIAN = "debian"


class CaTrustError(Exception):
    """Raised when the CA cannot be installed into or removed from the store."""


WhichFn = Callable[[str], Optional[str]]


@dataclass(frozen=True)
class InstallOutcome:
    """What ``install()`` did, for reporting and idempotency assertions."""

    kind: TrustStoreKind
    anchor_path: Path


def detect(
    which: WhichFn | None = None,
) -> tuple[TrustStoreKind, Path, str] | None:
    """Return the trust-store flavour, anchor dir and update command.

    Probes by directory + tool existence rather than reading
    ``/etc/os-release``: an unknown or minimal distribution then degrades to
    ``None`` (caller skips CA install) instead of getting a hard rejection.

    Returns ``None`` when neither layout is present.
    """
    which = which or shutil.which

    if _RPM_ANCHOR_DIR.is_dir() and which(_RPM_UPDATE_CMD) is not None:
        return (TrustStoreKind.RPM, _RPM_ANCHOR_DIR, _RPM_UPDATE_CMD)
    if _DEBIAN_ANCHOR_DIR.is_dir() and which(_DEBIAN_UPDATE_CMD) is not None:
        return (TrustStoreKind.DEBIAN, _DEBIAN_ANCHOR_DIR, _DEBIAN_UPDATE_CMD)
    return None


def _anchor_basename(kind: TrustStoreKind) -> str:
    """Distros disagree on the extension; keep the naming in one place."""
    if kind == TrustStoreKind.RPM:
        return _ANCHOR_BASENAME_RPM
    return _ANCHOR_BASENAME_DEBIAN


class SystemTrustStore:
    """Install and remove the gateway CA in the host trust store.

    The store's contents change atomically at the anchor-file boundary: writing
    a file then running the update command is what makes the CA visible to
    clients. Failure at the update step must roll back the anchor file so the
    next reconcile is not observing a partial state.
    """

    def __init__(
        self,
        runner: CommandRunner | None = None,
        which: WhichFn | None = None,
        anchor_dir: Path | None = None,
        update_cmd: str | None = None,
        kind: TrustStoreKind | None = None,
    ) -> None:
        self._run = runner or run_command
        self._which = which or shutil.which
        # Allow the caller to short-circuit detection (used by tests and by
        # future reinstall paths that already know the layout).
        if anchor_dir is not None and update_cmd is not None and kind is not None:
            self._kind: TrustStoreKind | None = kind
            self._anchor_dir: Path | None = anchor_dir
            self._update_cmd: str | None = update_cmd
        else:
            detected = detect(which=self._which)
            if detected is None:
                self._kind = None
                self._anchor_dir = None
                self._update_cmd = None
            else:
                self._kind, self._anchor_dir, self._update_cmd = detected

    @property
    def kind(self) -> TrustStoreKind | None:
        return self._kind

    def is_available(self) -> bool:
        """Whether the host has a recognised trust store."""
        return self._kind is not None

    def anchor_path(self) -> Path | None:
        """Where our anchor file would live, if the store is recognised."""
        if self._kind is None or self._anchor_dir is None:
            return None
        return self._anchor_dir / _anchor_basename(self._kind)

    def is_installed(self) -> bool:
        """Whether our anchor file is currently present on disk.

        Checks the file, not the extracted bundle. If the operator ran
        ``update-ca-trust`` externally after we placed the anchor, the bundle
        may lag; we still report ``True`` because the source of truth is the
        anchor directory.
        """
        anchor = self.anchor_path()
        return anchor is not None and anchor.exists()

    def install(self, ca_pem_path: Path) -> InstallOutcome:
        """Copy the CA into the anchor directory and refresh the trust bundle.

        # Errors
        Raises ``CaTrustError`` when the host has no recognised trust store,
        when the source CA is missing, or when the update command fails. On
        update failure the anchor file is removed so the next reconcile does
        not see a half-installed state.
        """
        if self._kind is None or self._anchor_dir is None or self._update_cmd is None:
            raise CaTrustError(
                "no recognised system trust store on this host "
                "(neither /etc/pki/ca-trust/source/anchors/ + update-ca-trust "
                "nor /usr/local/share/ca-certificates/ + update-ca-certificates "
                "were found)"
            )
        if not ca_pem_path.is_file():
            raise CaTrustError(
                f"source CA file {ca_pem_path} does not exist; "
                "has mitmdump generated its CA yet?"
            )

        anchor = self._anchor_dir / _anchor_basename(self._kind)
        # Copy first, then update: doing it the other way round would refresh
        # the bundle without our CA in it.
        try:
            _copy_root_readonly(ca_pem_path, anchor)
        except OSError as exc:
            raise CaTrustError(f"cannot write CA anchor to {anchor}: {exc}") from exc

        result = self._run([self._update_cmd])
        if not result.ok:
            # Roll back the anchor so a retry starts from a clean slate.
            _unlink_ignore_missing(anchor)
            raise CaTrustError(
                f"{self._update_cmd} failed after installing CA anchor: "
                f"{result.stderr.strip() or result.returncode}"
            )

        logger.info(
            "secret gateway CA installed into system trust store: " "kind=%s anchor=%s",
            self._kind.value,
            anchor,
        )
        return InstallOutcome(kind=self._kind, anchor_path=anchor)

    def uninstall(self) -> bool:
        """Remove our anchor and refresh the bundle. Returns whether anything changed.

        Missing anchor is not an error: teardown must be idempotent because the
        daemon may have crashed mid-install last time.
        """
        anchor = self.anchor_path()
        if anchor is None or not anchor.exists():
            return False

        _unlink_ignore_missing(anchor)
        # Refresh even if we could not confirm the anchor was there before --
        # the bundle needs to stop shipping our cert.
        if self._update_cmd is not None:
            result = self._run([self._update_cmd])
            if not result.ok:
                # Keep going; a stale bundle is a smaller problem than a
                # daemon that refuses to stop. The next reconcile will retry.
                logger.warning(
                    "%s failed after removing CA anchor %s: %s",
                    self._update_cmd,
                    anchor,
                    result.stderr.strip() or result.returncode,
                )
        logger.info("secret gateway CA removed from system trust store: %s", anchor)
        return True


def publish_readable_copy(
    ca_pem_path: Path,
    dest: Path,
) -> None:
    """Copy the CA public certificate to a world-readable location.

    ``mitm-ca/`` sits under the mitmproxy confdir, which also contains the
    private key. That directory is 0700 root:root by design, so the agent
    (running as an unprivileged user) cannot read anything inside. Copy just
    the public cert out so agents that need to reference the file directly --
    typically via ``SSL_CERT_FILE=...`` -- can do so without opening a hole in
    the confdir permissions.

    Idempotent: rewrites the file every call so operators can force a refresh
    by restarting the daemon.

    # Errors
    Raises ``CaTrustError`` when the source is missing or the destination
    cannot be written.
    """
    if not ca_pem_path.is_file():
        raise CaTrustError(
            f"source CA file {ca_pem_path} does not exist; "
            "has mitmdump generated its CA yet?"
        )
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _copy_root_readonly(ca_pem_path, dest)
    except OSError as exc:
        raise CaTrustError(f"cannot publish CA to {dest}: {exc}") from exc

    logger.info("secret gateway CA published to agent-readable path: %s", dest)


def _copy_root_readonly(src: Path, dest: Path) -> None:
    """Copy *src* to *dest* with ``0644 root:root``.

    ``shutil.copy`` preserves neither permissions nor ownership predictably.
    Writing through a fresh file with an explicit ``chmod`` keeps the target
    well-defined regardless of whether *dest* already existed.
    """
    data = src.read_bytes()
    dest.write_bytes(data)
    os.chmod(dest, 0o644)
    # chown to root:root only when we actually run as root. Tests run as the
    # invoking user and would otherwise fail with PermissionError; production
    # daemons are root, so the chown succeeds and the file is well-defined.
    if os.geteuid() == 0:
        os.chown(dest, 0, 0)


def _unlink_ignore_missing(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("could not remove %s: %s", path, exc)
