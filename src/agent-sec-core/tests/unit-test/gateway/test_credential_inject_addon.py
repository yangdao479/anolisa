"""Unit tests for the secret gateway credential injection addon.

The addon is normally loaded by mitmproxy's bundled interpreter, so mitmproxy is
not importable here. A minimal stub of the surface the addon touches
(``Request.headers``, the ``Request.query`` MultiDictView and ``Flow.metadata``)
is installed before importing it, which keeps these tests runnable in plain
``make test`` without installing mitmproxy at all.

The query stub mirrors real mitmproxy semantics that the addon depends on:
``get_all`` / ``set_all`` for repeated parameters, and writes to the view
rewriting ``Request.path``.
"""

import json
import logging
import sys
import types
import urllib.parse

import pytest

FAKE_GH = "ghp_0000000000000000000000000000AGENTSEC"
REAL_GH = "ghp_REALgithubvalue00000000000000000"
FAKE_GT = "REPLACEME000000000000000AGENTSEC"
REAL_GT = "REALgiteevalue0000000000000000"


class _StubQueryView:
    """Mutable query view whose writes rewrite the request path."""

    def __init__(self, request: "_StubRequest") -> None:
        self._request = request

    def _pairs(self) -> list[tuple[str, str]]:
        query = urllib.parse.urlparse(self._request.path).query
        return urllib.parse.parse_qsl(query, keep_blank_values=True)

    def get_all(self, key: str) -> list[str]:
        return [value for name, value in self._pairs() if name == key]

    def set_all(self, key: str, values: list[str]) -> None:
        rebuilt: list[tuple[str, str]] = []
        replaced = False
        for name, value in self._pairs():
            if name == key:
                if not replaced:
                    rebuilt.extend((key, new) for new in values)
                    replaced = True
            else:
                rebuilt.append((name, value))
        if not replaced:
            rebuilt.extend((key, new) for new in values)

        parsed = urllib.parse.urlparse(self._request.path)
        self._request.path = urllib.parse.urlunparse(
            ("", "", parsed.path, "", urllib.parse.urlencode(rebuilt), "")
        )


class _StubRequest:
    def __init__(
        self,
        host: str,
        path: str,
        headers: dict[str, str] | None = None,
        method: str = "GET",
    ) -> None:
        self.pretty_host = host
        self.path = path
        self.method = method
        self.headers = dict(headers or {})
        self.timestamp_start = 0.0

    @property
    def query(self) -> _StubQueryView:
        return _StubQueryView(self)


class _StubFlow:
    def __init__(self, request: _StubRequest) -> None:
        self.request = request
        self.metadata: dict[str, object] = {}
        self.response = None
        self.error = None


def _install_mitmproxy_stub() -> None:
    """Register a stub ``mitmproxy.http`` before the addon is imported."""
    if "mitmproxy" in sys.modules:
        return
    package = types.ModuleType("mitmproxy")
    http_module = types.ModuleType("mitmproxy.http")
    http_module.HTTPFlow = _StubFlow
    package.http = http_module
    sys.modules["mitmproxy"] = package
    sys.modules["mitmproxy.http"] = http_module


_install_mitmproxy_stub()

from agent_sec_cli.gateway.credential_inject_addon import (  # noqa: E402
    CredentialInjector,
    _Credential,
)


def _credential(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "cred",
        "fake_token": "fake-value",
        "real_token": "real-value",
        "hosts": ["api.example.com"],
    }
    base.update(overrides)
    return base


@pytest.fixture
def injector() -> CredentialInjector:
    """Injector with one header-carried and one query-carried credential."""
    instance = CredentialInjector()
    instance.credentials = [
        _Credential(
            _credential(
                id="github-token",
                fake_token=FAKE_GH,
                real_token=REAL_GH,
                hosts=["api.github.com"],
            ),
            0,
        ),
        _Credential(
            _credential(
                id="gitee-token",
                fake_token=FAKE_GT,
                real_token=REAL_GT,
                hosts=["gitee.com"],
                location="query",
                param="access_token",
            ),
            1,
        ),
    ]
    return instance


# -- header carrier --------------------------------------------------------


def test_header_placeholder_is_replaced(injector: CredentialInjector) -> None:
    flow = _StubFlow(
        _StubRequest("api.github.com", "/user", {"Authorization": f"Bearer {FAKE_GH}"})
    )
    injector.request(flow)

    assert flow.request.headers["Authorization"] == f"Bearer {REAL_GH}"
    assert flow.metadata["agent_sec_injected"] is True
    assert flow.metadata["agent_sec_carrier"] == "header:Authorization"


def test_header_scheme_keyword_survives(injector: CredentialInjector) -> None:
    """Substring replacement must cover `token <t>` as well as `Bearer <t>`."""
    flow = _StubFlow(
        _StubRequest("api.github.com", "/user", {"Authorization": f"token {FAKE_GH}"})
    )
    injector.request(flow)

    assert flow.request.headers["Authorization"] == f"token {REAL_GH}"


def test_unmanaged_host_is_not_rewritten(injector: CredentialInjector) -> None:
    flow = _StubFlow(
        _StubRequest("evil.example.com", "/x", {"Authorization": f"Bearer {FAKE_GH}"})
    )
    injector.request(flow)

    assert flow.request.headers["Authorization"] == f"Bearer {FAKE_GH}"
    assert flow.metadata["agent_sec_injected"] is False


# -- query carrier ---------------------------------------------------------


def test_query_placeholder_is_replaced_and_siblings_kept(
    injector: CredentialInjector,
) -> None:
    flow = _StubFlow(
        _StubRequest("gitee.com", f"/api/v5/user?page=2&access_token={FAKE_GT}&per=5")
    )
    injector.request(flow)

    query = urllib.parse.parse_qs(urllib.parse.urlparse(flow.request.path).query)
    assert query["access_token"] == [REAL_GT]
    assert query["page"] == ["2"]
    assert query["per"] == ["5"]
    assert flow.metadata["agent_sec_carrier"] == "query:access_token"


def test_query_repeated_parameter_all_replaced(
    injector: CredentialInjector,
) -> None:
    flow = _StubFlow(
        _StubRequest("gitee.com", f"/x?access_token={FAKE_GT}&access_token={FAKE_GT}")
    )
    injector.request(flow)

    query = urllib.parse.parse_qs(urllib.parse.urlparse(flow.request.path).query)
    assert query["access_token"] == [REAL_GT, REAL_GT]


def test_query_without_placeholder_passes_through(
    injector: CredentialInjector,
) -> None:
    flow = _StubFlow(_StubRequest("gitee.com", "/api/v5/user?access_token=unrelated"))
    injector.request(flow)

    assert "unrelated" in flow.request.path
    assert flow.metadata["agent_sec_injected"] is False


# -- leak prevention -------------------------------------------------------


def test_real_credential_never_reaches_the_record(
    injector: CredentialInjector,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A query-carried credential lands in the path; the record must scrub it.

    This is the regression guard for the leak that query support introduced:
    ``path`` looks like an innocuous field but carries the secret once injected,
    and it is both logged and forwarded to the audit store.
    """
    flow = _StubFlow(_StubRequest("gitee.com", f"/api/v5/user?access_token={FAKE_GT}"))
    injector.request(flow)
    assert REAL_GT in flow.request.path, "precondition: path holds the real token"

    injector.audit = None
    flow.response = types.SimpleNamespace(status_code=200, timestamp_end=1.0)
    with caplog.at_level(logging.INFO, logger="agent_sec.gateway"):
        injector.response(flow)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert REAL_GT not in logged
    assert REAL_GH not in logged
    assert "<gitee-token:redacted>" in logged

    # The structured record must still be usable JSON for the audit path.
    payload = json.loads(logged.split("secret_gateway_flow ", 1)[1])
    assert payload["credential_id"] == "gitee-token"
    assert payload["injected"] is True


# -- configuration validation ---------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"location": "query"}, "query carrier without param"),
        ({"location": "query", "param": "k", "header": "X"}, "query with header"),
        ({"param": "k"}, "header carrier with param"),
        ({"location": "body"}, "unsupported location"),
        ({"real_token": "fake-value"}, "real equals fake"),
        ({"id": ""}, "missing id"),
        ({"real_token": ""}, "missing real_token"),
        ({"hosts": []}, "empty hosts"),
    ],
)
def test_invalid_credential_is_rejected(
    overrides: dict[str, object], reason: str
) -> None:
    with pytest.raises(ValueError):
        _Credential(_credential(**overrides), 0)


def test_credential_defaults_to_authorization_header() -> None:
    credential = _Credential(_credential(), 0)

    assert credential.location == "header"
    assert credential.header == "Authorization"
    assert credential.carrier() == "header:Authorization"


def test_host_matching_is_case_insensitive() -> None:
    credential = _Credential(_credential(hosts=["API.Example.COM"]), 0)

    assert credential.matches_host("api.example.com")
    assert credential.matches_host("API.EXAMPLE.COM")
    assert not credential.matches_host("other.example.com")
