"""Unit tests for system trust store injection of the gateway CA.

No host trust store is touched: the update command runner and ``shutil.which``
are injected, and the anchor directory is redirected into ``tmp_path``. That
keeps the rollout assertions meaningful on a developer machine while covering
the parts most likely to be wrong -- distro detection, rollback on a failed
update, and idempotent teardown.
"""

import os
from collections.abc import Sequence
from pathlib import Path

import pytest
from agent_sec_cli.gateway.ca_trust import (
    CaTrustError,
    SystemTrustStore,
    TrustStoreKind,
    detect,
    publish_readable_copy,
)
from agent_sec_cli.gateway.nat_rules import CommandResult

_CA_BODY = (
    b"-----BEGIN CERTIFICATE-----\nfake-ca-for-tests\n-----END CERTIFICATE-----\n"
)


class _FakeRunner:
    """Records argv and replays queued results."""

    def __init__(self, results: list[CommandResult] | None = None) -> None:
        self.calls: list[list[str]] = []
        self._results = list(results or [])

    def __call__(self, command: Sequence[str]) -> CommandResult:
        self.calls.append(list(command))
        if self._results:
            return self._results.pop(0)
        return CommandResult(returncode=0)


def _ca_file(tmp_path: Path) -> Path:
    path = tmp_path / "mitmproxy-ca-cert.pem"
    path.write_bytes(_CA_BODY)
    return path


def _store(
    tmp_path: Path,
    kind: TrustStoreKind,
    runner: _FakeRunner,
) -> tuple[SystemTrustStore, Path]:
    """Build a store whose anchor directory lives under tmp_path."""
    anchor_dir = tmp_path / "anchors"
    anchor_dir.mkdir(exist_ok=True)
    update_cmd = (
        "update-ca-trust" if kind == TrustStoreKind.RPM else "update-ca-certificates"
    )
    store = SystemTrustStore(
        runner=runner,
        anchor_dir=anchor_dir,
        update_cmd=update_cmd,
        kind=kind,
    )
    return store, anchor_dir


# -- distro detection ------------------------------------------------------


def test_detect_returns_none_without_a_recognised_store() -> None:
    """An unknown distro must degrade rather than raise, so daemon start survives."""
    assert detect(which=lambda _: None) is None


def test_detect_reports_no_store_when_tool_is_missing(monkeypatch) -> None:
    """The anchor directory alone is not enough; the update tool must exist too."""
    monkeypatch.setattr(Path, "is_dir", lambda self: True)

    assert detect(which=lambda _: None) is None


def test_detect_prefers_rpm_layout(monkeypatch) -> None:
    monkeypatch.setattr(Path, "is_dir", lambda self: True)

    result = detect(which=lambda name: f"/usr/bin/{name}")

    assert result is not None
    kind, anchor_dir, update_cmd = result
    assert kind == TrustStoreKind.RPM
    assert anchor_dir == Path("/etc/pki/ca-trust/source/anchors")
    assert update_cmd == "update-ca-trust"


def test_detect_falls_back_to_debian_layout(monkeypatch) -> None:
    """When only the Debian tool is present, the Debian layout must be chosen."""
    monkeypatch.setattr(Path, "is_dir", lambda self: True)

    result = detect(
        which=lambda name: (
            "/usr/sbin/update-ca-certificates"
            if name == "update-ca-certificates"
            else None
        )
    )

    assert result is not None
    kind, anchor_dir, update_cmd = result
    assert kind == TrustStoreKind.DEBIAN
    assert anchor_dir == Path("/usr/local/share/ca-certificates")
    assert update_cmd == "update-ca-certificates"


def test_store_without_layout_is_unavailable() -> None:
    store = SystemTrustStore(runner=_FakeRunner(), which=lambda _: None)

    assert store.is_available() is False
    assert store.kind is None
    assert store.anchor_path() is None
    assert store.is_installed() is False


# -- install ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "expected_name", "expected_cmd"),
    [
        (TrustStoreKind.RPM, "agent-sec-gateway.pem", "update-ca-trust"),
        (TrustStoreKind.DEBIAN, "agent-sec-gateway.crt", "update-ca-certificates"),
    ],
)
def test_install_writes_anchor_and_refreshes_bundle(
    tmp_path: Path,
    kind: TrustStoreKind,
    expected_name: str,
    expected_cmd: str,
) -> None:
    runner = _FakeRunner()
    store, anchor_dir = _store(tmp_path, kind, runner)

    outcome = store.install(_ca_file(tmp_path))

    anchor = anchor_dir / expected_name
    assert outcome.kind == kind
    assert outcome.anchor_path == anchor
    assert anchor.read_bytes() == _CA_BODY
    # Extension differs per distro because update-ca-certificates only picks up
    # .crt files; getting this wrong installs a file nothing reads.
    assert runner.calls == [[expected_cmd]]


def test_installed_anchor_is_world_readable(tmp_path: Path) -> None:
    """Clients read the bundle, but a 0600 anchor would break the update tool."""
    store, anchor_dir = _store(tmp_path, TrustStoreKind.RPM, _FakeRunner())

    store.install(_ca_file(tmp_path))

    mode = (anchor_dir / "agent-sec-gateway.pem").stat().st_mode
    assert oct(mode)[-3:] == "644"


def test_install_is_idempotent(tmp_path: Path) -> None:
    runner = _FakeRunner()
    store, _ = _store(tmp_path, TrustStoreKind.RPM, runner)
    ca = _ca_file(tmp_path)

    store.install(ca)
    store.install(ca)

    assert store.is_installed() is True
    assert len(runner.calls) == 2


def test_install_rolls_back_when_update_fails(tmp_path: Path) -> None:
    """A leftover anchor with a stale bundle is a half-installed state."""
    runner = _FakeRunner([CommandResult(returncode=1, stderr="boom")])
    store, anchor_dir = _store(tmp_path, TrustStoreKind.RPM, runner)

    with pytest.raises(CaTrustError, match="boom"):
        store.install(_ca_file(tmp_path))

    assert not (anchor_dir / "agent-sec-gateway.pem").exists()
    assert store.is_installed() is False


def test_install_without_a_store_is_rejected(tmp_path: Path) -> None:
    store = SystemTrustStore(runner=_FakeRunner(), which=lambda _: None)

    with pytest.raises(CaTrustError, match="no recognised system trust store"):
        store.install(_ca_file(tmp_path))


def test_install_reports_missing_source_ca(tmp_path: Path) -> None:
    """Points at the real cause: mitmdump has not generated its CA yet."""
    store, _ = _store(tmp_path, TrustStoreKind.RPM, _FakeRunner())

    with pytest.raises(CaTrustError, match="does not exist"):
        store.install(tmp_path / "absent.pem")


# -- uninstall -------------------------------------------------------------


def test_uninstall_removes_anchor_and_refreshes(tmp_path: Path) -> None:
    runner = _FakeRunner()
    store, anchor_dir = _store(tmp_path, TrustStoreKind.RPM, runner)
    store.install(_ca_file(tmp_path))
    runner.calls.clear()

    assert store.uninstall() is True
    assert not (anchor_dir / "agent-sec-gateway.pem").exists()
    assert runner.calls == [["update-ca-trust"]]


def test_uninstall_is_idempotent(tmp_path: Path) -> None:
    """Teardown must survive a daemon that crashed mid-install."""
    runner = _FakeRunner()
    store, _ = _store(tmp_path, TrustStoreKind.RPM, runner)

    assert store.uninstall() is False
    assert runner.calls == []


def test_uninstall_tolerates_a_failing_update_command(tmp_path: Path) -> None:
    """A stale bundle must not stop the daemon from shutting down."""
    store, anchor_dir = _store(tmp_path, TrustStoreKind.RPM, _FakeRunner())
    store.install(_ca_file(tmp_path))
    store._run = _FakeRunner([CommandResult(returncode=2, stderr="nope")])

    assert store.uninstall() is True
    assert not (anchor_dir / "agent-sec-gateway.pem").exists()


# -- agent-readable copy ---------------------------------------------------


def test_publish_readable_copy_is_agent_readable(tmp_path: Path) -> None:
    """The confdir is root-only because it holds the key; the copy must not be."""
    dest = tmp_path / "published" / "ca-cert.pem"

    publish_readable_copy(_ca_file(tmp_path), dest)

    assert dest.read_bytes() == _CA_BODY
    assert oct(dest.stat().st_mode)[-3:] == "644"


def test_publish_readable_copy_creates_parent_directory(tmp_path: Path) -> None:
    dest = tmp_path / "a" / "b" / "ca-cert.pem"

    publish_readable_copy(_ca_file(tmp_path), dest)

    assert dest.is_file()


def test_publish_readable_copy_overwrites_existing(tmp_path: Path) -> None:
    """Restarting the daemon must be enough to refresh a rotated CA."""
    dest = tmp_path / "ca-cert.pem"
    dest.write_bytes(b"stale")

    publish_readable_copy(_ca_file(tmp_path), dest)

    assert dest.read_bytes() == _CA_BODY


def test_publish_readable_copy_reports_missing_source(tmp_path: Path) -> None:
    with pytest.raises(CaTrustError, match="does not exist"):
        publish_readable_copy(tmp_path / "absent.pem", tmp_path / "out.pem")


def test_publish_readable_copy_reports_unwritable_destination(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(CaTrustError, match="cannot publish CA"):
            publish_readable_copy(_ca_file(tmp_path), locked / "ca-cert.pem")
    finally:
        locked.chmod(0o700)


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root can write anywhere, so the guard cannot be observed"
)
def test_publish_skips_chown_when_not_root(tmp_path: Path) -> None:
    """Non-root must not fail on chown; production daemons are root and do chown."""
    dest = tmp_path / "ca-cert.pem"

    publish_readable_copy(_ca_file(tmp_path), dest)

    assert dest.is_file()
