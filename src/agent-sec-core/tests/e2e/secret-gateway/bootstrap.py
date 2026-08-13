"""Bootstrap a secret gateway config for a test environment.

Writes a single file: the addon's ``config.json``, containing a PAT-shaped fake
token for the agent and the real credential inline.

The real token may itself be an inert value: the E2E test only proves the chain
(TLS termination -> injection -> re-issued request), and an upstream 401 does
not weaken that. Using an inert value keeps a live credential out of the test
environment entirely.

TEST ONLY. Not for production:

* ``--real-token`` puts the credential in argv, so it lands in shell history
  and is briefly visible in ``ps`` output to any local user;
* it overwrites the whole config rather than editing it.

Production deployments hand-write the config -- see
``docs/design/SECRET_GATEWAY_CONFIG_zh.md``.

Lives under ``tests/`` rather than in the shipped package precisely because of
the two hazards above: a tool that overwrites ``/etc`` config as root has no
business being installed on a production host.

Usage::

    python3 tests/e2e/secret-gateway/bootstrap.py --host api.github.com
"""

import argparse
import json
import os
import pathlib
import sys

# Run standalone (`python3 tests/e2e/secret-gateway/bootstrap.py`) without the
# package installed, matching how the other e2e scripts resolve their imports.
_PACKAGE_SRC = pathlib.Path(__file__).resolve().parents[3] / "agent-sec-cli" / "src"
if str(_PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_SRC))

from agent_sec_cli.gateway.fake_token import (  # noqa: E402
    generate_fake_token_like,
    generate_github_fake_token,
)

DEFAULT_CONFIG_DIR = "/etc/agent-sec/gateway"
DEFAULT_LOG_PATH = "/var/log/agent-sec/gateway.log"
DEFAULT_CREDENTIAL_ID = "github-token"
DEFAULT_HOSTS = ("api.github.com",)


def _write_config(path: str, config: dict[str, object]) -> None:
    """Write *config* to *path* as a root-owned 0600 file.

    # Errors
    Raises ``PermissionError`` when the resulting file is not owned by root.

    The config now carries the real credential inline, so it is a secret file:
    it is created with 0600 from the start (never briefly group/world readable)
    and its ownership is verified afterwards. 0600 alone is not enough -- a
    0600 file owned by the agent's own uid is perfectly readable by the agent,
    and ownership is the half that actually keeps it out of reach.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        handle = os.fdopen(fd, "w", encoding="utf-8")
    except BaseException:
        # fdopen did not take ownership of the descriptor, so close it here.
        # Once it succeeds, the with-block below owns and closes it.
        os.close(fd)
        raise
    with handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")

    owner_uid = os.stat(path).st_uid
    if owner_uid != 0:
        os.unlink(path)
        raise PermissionError(
            f"{path} would be owned by uid {owner_uid}, not root; "
            "the agent could read the credential it contains. Re-run as root."
        )


def main(argv: list[str] | None = None) -> int:
    """Generate a gateway config with a fake token and an inline real token."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real-token",
        help=(
            "credential injected on egress. Omit to generate an inert "
            "placeholder, which is the recommended choice for chain-only "
            "validation. Avoid on shared hosts: argv is visible via ps."
        ),
    )
    parser.add_argument("--config-dir", default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--credential-id", default=DEFAULT_CREDENTIAL_ID)
    parser.add_argument(
        "--host",
        action="append",
        dest="hosts",
        help=(
            "host this credential may be injected into; repeatable. "
            f"Defaults to {', '.join(DEFAULT_HOSTS)}."
        ),
    )
    parser.add_argument("--daemon-socket", default="")
    parser.add_argument("--log-path", default=DEFAULT_LOG_PATH)
    parser.add_argument(
        "--keep-prefix",
        type=int,
        default=None,
        help=(
            "number of leading characters of the real token to preserve "
            "verbatim in the placeholder. Only needed for providers whose "
            "prefix has no separator, e.g. --keep-prefix 4 for Google's AIza."
        ),
    )
    parser.add_argument(
        "--print-fake-token",
        action="store_true",
        help="print only the fake token (for shell capture)",
    )
    args = parser.parse_args(argv)

    # Refuse to run unprivileged: the config holds the real credential, and a
    # config owned by the invoking user is readable by an agent on that uid.
    if os.geteuid() != 0:
        sys.stderr.write(
            "secret gateway bootstrap must run as root, otherwise the config "
            "would be owned by the invoking user and its inline credential "
            "readable by an agent running under that same uid.\n"
        )
        return 1

    hosts = args.hosts or list(DEFAULT_HOSTS)

    if args.real_token:
        real_token = args.real_token
        # Derive the placeholder from the real credential so it matches whatever
        # provider is in play, instead of assuming a GitHub PAT.
        fake_token = generate_fake_token_like(real_token, keep_prefix=args.keep_prefix)
    else:
        # No real credential to copy the shape from; fall back to a GitHub PAT
        # shape, which is what the E2E fixtures use.
        real_token = generate_github_fake_token(marker="REALISH")
        fake_token = generate_github_fake_token()

    config = {
        "schema_version": 1,
        "log_path": args.log_path,
        "daemon_socket": args.daemon_socket,
        "credentials": [
            {
                "id": args.credential_id,
                "fake_token": fake_token,
                "real_token": real_token,
                "hosts": hosts,
                "header": "Authorization",
            }
        ],
    }

    config_path = os.path.join(args.config_dir, "config.json")
    _write_config(config_path, config)

    if args.print_fake_token:
        sys.stdout.write(fake_token + "\n")
        return 0

    sys.stdout.write(
        "secret gateway bootstrap complete\n"
        f"  config:     {config_path} (0600, root)\n"
        f"  hosts:      {', '.join(hosts)}\n"
        f"  fake token: {fake_token}\n"
        "\nGive the agent only the fake token, for example:\n"
        f"  export GITHUB_TOKEN={fake_token}\n"
        "\nThe real credential is inline in the config, so that file is a "
        "secret:\n  never copy it into a ticket, a chat message or version "
        "control.\n"
    )
    if args.real_token is None:
        sys.stdout.write(
            "\nNote: no --real-token given, so an inert placeholder was "
            "generated.\n      Upstream will answer 401; that is expected and "
            "still proves the chain.\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
