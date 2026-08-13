"""Mock HTTPS upstream that echoes the credential it received.

This is the assertion instrument for the E2E test. Hitting the real GitHub only
proves the request went somewhere -- it cannot show which token left the box. An
echo upstream can, so the test can assert that the header arriving upstream
carries the real token and not the placeholder.

Serves HTTPS (self-signed, generated on first start) because the point is to
exercise the proxy's TLS termination, not to bypass it.

Standard library only, so it can run under any interpreter present in the test
container.

Usage::

    python3 mock_echo_upstream.py --port 8443 --host 0.0.0.0
"""

import argparse
import hashlib
import json
import os
import ssl
import subprocess
import sys
import tempfile
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8443
DEFAULT_CERT_DIR = "/var/lib/agent-sec/mock-upstream"
CERT_COMMON_NAME = "echo.local"


def _generate_self_signed(cert_dir: str) -> tuple[str, str]:
    """Return (cert_path, key_path), generating a self-signed pair if absent.

    Uses the openssl CLI rather than a Python TLS library so this stays
    dependency-free.

    # Errors
    Raises ``RuntimeError`` when openssl is unavailable or fails.
    """
    os.makedirs(cert_dir, exist_ok=True)
    cert_path = os.path.join(cert_dir, "echo-cert.pem")
    key_path = os.path.join(cert_dir, "echo-key.pem")
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return cert_path, key_path

    completed = subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "365",
            "-subj",
            f"/CN={CERT_COMMON_NAME}",
            "-addext",
            f"subjectAltName=DNS:{CERT_COMMON_NAME},DNS:localhost,IP:127.0.0.1",
            "-keyout",
            key_path,
            "-out",
            cert_path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"openssl failed: {completed.stderr.strip()}")
    os.chmod(key_path, 0o600)
    return cert_path, key_path


class _EchoHandler(BaseHTTPRequestHandler):
    """Return the request's credential headers as JSON."""

    server_version = "agent-sec-mock-echo/1.0"
    # Default to masked: this tool sits on the receiving end of real egress, so
    # a careless run against a live credential must not echo it back.
    show_authorization = False

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        """Echo the received credential headers."""
        self._respond()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        """Echo the received credential headers."""
        self._respond()

    def _respond(self) -> None:
        authorization = self.headers.get("Authorization", "")
        path_only, _, query_string = self.path.partition("?")
        query = urllib.parse.parse_qs(query_string, keep_blank_values=True)

        payload: dict[str, object] = {
            "path": path_only,
            "method": self.command,
            "host_header": self.headers.get("Host", ""),
            "authorization_present": bool(authorization),
            "authorization_sha256": (
                hashlib.sha256(authorization.encode("utf-8")).hexdigest()
                if authorization
                else ""
            ),
            # Names only: enough to tell which parameter carried a credential
            # without printing its value.
            "query_keys": sorted(query),
        }

        # Credentials arrive in one of two places, and *both* must obey the same
        # switch: the Authorization header, and -- for query-carried credentials
        # -- the query string itself. Echoing verbatim is what lets a human
        # confirm the real token was swapped in, so it is opt-in and only safe
        # when the "real" token is an inert test value.
        if self.show_authorization:
            payload["authorization"] = authorization
            payload["query"] = {key: values for key, values in query.items()}
            payload["full_path"] = self.path
        else:
            payload["authorization"] = "<masked>"
            payload["query"] = "<masked>"

        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        """Log to stderr without the default timestamp noise."""
        sys.stderr.write("mock-echo %s\n" % (fmt % args))


def main(argv: list[str] | None = None) -> int:
    """Run the mock echo upstream."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--cert-dir", default=DEFAULT_CERT_DIR)
    parser.add_argument(
        "--show-authorization",
        action="store_true",
        help=(
            "echo credential-bearing fields verbatim: the Authorization header, "
            "the query string and the full path. This is how you confirm the "
            "real token was injected. Only use when that credential is an inert "
            "test value; default masks them and reports the sha256 instead."
        ),
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="serve plain HTTP (skips TLS; only for isolating non-TLS issues)",
    )
    args = parser.parse_args(argv)

    handler = type(
        "_Handler",
        (_EchoHandler,),
        {"show_authorization": args.show_authorization},
    )
    httpd = ThreadingHTTPServer((args.host, args.port), handler)

    scheme = "http"
    if not args.http:
        cert_dir = args.cert_dir
        try:
            cert_path, key_path = _generate_self_signed(cert_dir)
        except (OSError, RuntimeError) as exc:
            # Fall back to a temp dir so the tool still works when the default
            # location is not writable.
            fallback = tempfile.mkdtemp(prefix="agent-sec-mock-echo-")
            sys.stderr.write(
                f"mock-echo cert dir {cert_dir} unusable ({exc}); using {fallback}\n"
            )
            cert_path, key_path = _generate_self_signed(fallback)

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert_path, keyfile=key_path)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"

    sys.stderr.write(
        f"mock-echo listening on {scheme}://{args.host}:{args.port} "
        f"(show_authorization={args.show_authorization})\n"
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
