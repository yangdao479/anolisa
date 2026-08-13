#!/usr/bin/env bash
# Install the pinned mitmproxy standalone binary used by the secret gateway.
#
# Deployment-side script: it fetches, verifies and installs a runtime
# dependency onto the target host. It is deliberately independent of the two
# packaging outputs (anolisa-CLI raw packaging and RPM) so either can call it,
# and so an operator can run it directly on a host.
#
# mitmproxy is NOT a pip dependency: releases >= 11.1.0 require Python >= 3.12
# while agent-sec-cli is pinned to 3.11.6. The upstream standalone build bundles
# its own interpreter, so pinning the binary keeps mitmproxy out of uv.lock /
# requirements.txt and removes the cp311 wheel compatibility risk (notably the
# mitmproxy_rs Rust extension).
set -euo pipefail

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

usage() {
    die "usage: $0 BIN_DIR ARCHIVE_CACHE"
}

[ "$#" -eq 2 ] || usage

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$1"
ARCHIVE_CACHE="$2"
MITMPROXY_VERSION="12.2.3"
TARGET="linux-x86_64"
ARCHIVE_NAME="mitmproxy-${MITMPROXY_VERSION}-${TARGET}.tar.gz"
SOURCE_URL="https://downloads.mitmproxy.org/${MITMPROXY_VERSION}/${ARCHIVE_NAME}"
ARCHIVE_SHA256="2e95286b618fa6fd33e5e62a78c2e5112571d85f42ec2bac29b97ee242bdb5c5"
EXTRACTED_BINARY="mitmdump"
PROVENANCE_SOURCE="$SCRIPT_DIR/mitmproxy-provenance.toml"

command -v curl >/dev/null 2>&1 || die "curl is required to fetch mitmproxy"
command -v sha256sum >/dev/null 2>&1 || \
    die "sha256sum is required to verify mitmproxy"
[ -f "$PROVENANCE_SOURCE" ] || die "missing mitmproxy provenance metadata"

# Cross-check the pinned constants against provenance so the two can never
# drift apart silently.
grep -Fqx "mitmproxy_version = \"$MITMPROXY_VERSION\"" "$PROVENANCE_SOURCE" || \
    die "mitmproxy provenance version does not match $MITMPROXY_VERSION"
grep -Fqx "target = \"$TARGET\"" "$PROVENANCE_SOURCE" || \
    die "mitmproxy provenance target does not match $TARGET"
grep -Fqx "archive_filename = \"$ARCHIVE_NAME\"" "$PROVENANCE_SOURCE" || \
    die "mitmproxy provenance archive name does not match $ARCHIVE_NAME"
grep -Fqx "source_url = \"$SOURCE_URL\"" "$PROVENANCE_SOURCE" || \
    die "mitmproxy provenance URL does not match the pinned source"
grep -Fqx "archive_sha256 = \"$ARCHIVE_SHA256\"" "$PROVENANCE_SOURCE" || \
    die "mitmproxy provenance SHA-256 does not match the pinned archive"
grep -Fqx "extracted_binary = \"$EXTRACTED_BINARY\"" "$PROVENANCE_SOURCE" || \
    die "mitmproxy provenance binary name does not match $EXTRACTED_BINARY"

archive_parent="$(dirname "$ARCHIVE_CACHE")"
mkdir -p "$archive_parent" "$BIN_DIR"

work="$(mktemp -d "$archive_parent/.mitmproxy.XXXXXX")"
download="$(mktemp "$archive_parent/.${ARCHIVE_NAME}.XXXXXX")"
cleanup() {
    rm -rf "$work"
    rm -f "$download"
}
trap cleanup EXIT

# Reuse a cached archive only when its digest still matches the pin.
if [ ! -f "$ARCHIVE_CACHE" ] || \
    ! printf '%s  %s\n' "$ARCHIVE_SHA256" "$ARCHIVE_CACHE" | \
        sha256sum --check --status; then
    curl --fail --location --retry 2 --output "$download" "$SOURCE_URL"
    printf '%s  %s\n' "$ARCHIVE_SHA256" "$download" | \
        sha256sum --check --status || \
        die "mitmproxy archive SHA-256 does not match $ARCHIVE_SHA256"
    mv -f "$download" "$ARCHIVE_CACHE"
fi

# The upstream archive is flat: mitmproxy, mitmdump and mitmweb sit at the root.
tar -xzf "$ARCHIVE_CACHE" -C "$work" "$EXTRACTED_BINARY"
[ -x "$work/$EXTRACTED_BINARY" ] || \
    die "mitmproxy archive has no executable $EXTRACTED_BINARY"

install -p -m 0755 "$work/$EXTRACTED_BINARY" "$BIN_DIR/$EXTRACTED_BINARY.new"
mv -f "$BIN_DIR/$EXTRACTED_BINARY.new" "$BIN_DIR/$EXTRACTED_BINARY"

# PyInstaller single-file builds unpack into TMPDIR on every start, so a
# noexec /tmp makes the binary unrunnable. Probe with an explicit TMPDIR that
# is known to be executable rather than trusting the ambient one.
#
# `--version` routes through debug.dump_system_info(), whose first line is
# "Mitmproxy: <version>" -- match that prefix instead of the whole line so a
# build suffix cannot fail the check.
probe_tmp="$work/tmp"
mkdir -p "$probe_tmp"
version_line="$(
    TMPDIR="$probe_tmp" "$BIN_DIR/$EXTRACTED_BINARY" --version 2>/dev/null |
        head -n 1
)"
case "$version_line" in
    "Mitmproxy: $MITMPROXY_VERSION"*) ;;
    *) die "installed mitmdump reports '$version_line', expected Mitmproxy: $MITMPROXY_VERSION" ;;
esac

printf 'installed %s %s -> %s\n' \
    "$EXTRACTED_BINARY" "$MITMPROXY_VERSION" "$BIN_DIR/$EXTRACTED_BINARY"
