import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
import requests

from cost_monitoring.monitor import github_monitor
from cost_monitoring.monitor.copilot_broker import CopilotBrokerReader, CopilotBrokerUnavailable, CopilotTarget


@pytest.fixture
def wire():
    class Fixture:
        payload = b'{"organization":{"id":101,"login":"core"},"seat_breakdown":{"total":7}}'
        status = 200
        encoding = "identity"
        content_type = "application/json"

        def __init__(self):
            self.calls, self.identities, self.audiences = [], [], []

        def identity(self, audience):
            self.audiences.append(audience)
            value = ".".join(secrets.token_urlsafe(32) for _ in range(3))
            self.identities.append(value)
            return value

    f = Fixture()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            f.calls[-1]["body"] = json.loads(body)
            self.send_response(f.status)
            self.send_header("Content-Type", f.content_type)
            self.send_header("Content-Encoding", f.encoding)
            self.send_header("Content-Length", str(len(f.payload)))
            if f.status == 302:
                self.send_header("Location", "https://elsewhere.example.invalid/collect")
            self.end_headers()
            self.wfile.write(f.payload)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = requests.adapters.HTTPAdapter.send

    def send(adapter, request, **kwargs):
        f.calls.append(
            {
                "url": request.url,
                "method": request.method,
                "verified_tls": kwargs["verify"],
                "timeout": kwargs["timeout"],
                "proxies": kwargs["proxies"],
                "auth_bound": request.headers["Authorization"] == "Bearer " + f.identities[-1],
            }
        )
        # Route only the synthetic test host to an actual local HTTP server.
        # Production code has no override, HTTP mode or TLS-disable option.
        assert request.url == "https://broker.example.invalid/v1/copilot-billing"
        request.url = f"http://127.0.0.1:{server.server_port}/fixture"
        return original(adapter, request, **kwargs)

    f.target = CopilotTarget(101, "core", "core-total", "0" * 64)
    f.reader = CopilotBrokerReader("https://broker.example.invalid", (f.target,), f.identity)
    try:
        with patch.object(requests.adapters.HTTPAdapter, "send", send):
            yield f
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_native_http_binds_audience_and_target_without_personal_environment(wire, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.invalid:9")
    monkeypatch.setenv("GITHUB_TOKEN", secrets.token_urlsafe(32))
    monkeypatch.setenv("ENTERPRISE_GITHUB_TOKEN", secrets.token_urlsafe(32))
    assert wire.reader("CORE") == {"seats_assigned": 7, "seats_purchased": 7}
    assert wire.audiences == ["https://broker.example.invalid"]
    assert wire.calls == [
        {
            "url": "https://broker.example.invalid/v1/copilot-billing",
            "method": "POST",
            "verified_tls": True,
            "timeout": (5, 10),
            "proxies": {},
            "auth_bound": True,
            "body": {"grant_id": "core-total", "target_sha256": "0" * 64},
        }
    ]


def test_identity_provider_is_consulted_for_each_operation(wire):
    wire.reader("core")
    wire.reader("core")
    assert len(wire.identities) == 2
    assert wire.identities[0] != wire.identities[1]
    assert len(wire.calls) == 2
    assert all(call["auth_bound"] for call in wire.calls)


@pytest.mark.parametrize("status", [302, 401, 403, 404, 422, 429, 500, 503])
def test_failures_never_retry_skip_an_org_or_echo_provider_context(wire, status):
    wire.status, wire.payload = status, b'{"error":"PRIVATE_PROVIDER_CONTEXT"}'
    with pytest.raises(CopilotBrokerUnavailable, match="^copilot_broker_read_unavailable$"):
        wire.reader("core")
    assert len(wire.calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        b'{"organization":{"id":102,"login":"core"},"seat_breakdown":{"total":7}}',
        b'{"organization":{"id":101,"login":"other"},"seat_breakdown":{"total":7}}',
        b'{"organization":{"id":true,"login":"core"},"seat_breakdown":{"total":7}}',
        b'{"organization":{"id":101,"login":"core"},"seat_breakdown":{"total":true}}',
        b'{"organization":{"id":101,"login":"core"},"seat_breakdown":{"total":-1}}',
        b'{"organization":{"id":101,"login":"core"},"seat_breakdown":{"total":1,"total":2}}',
        b'{"organization":{"id":101,"login":"core"},"seat_breakdown":{"total":7},"identity":"PRIVATE"}',
        b'{"organization":{"id":101,"login":"core"},"seat_breakdown":{"total":2147483648}}',
        b'{"organization":{"id":101,"login":"core"},"seat_breakdown":{"total":NaN}}',
        b"x" * 4097,
    ],
)
def test_untrusted_or_oversized_envelopes_are_not_cost_data(wire, payload):
    wire.payload = payload
    with pytest.raises(CopilotBrokerUnavailable, match="^copilot_broker_read_unavailable$"):
        wire.reader("core")


def test_compression_content_type_and_total_read_deadline_are_bounded(wire):
    for encoding, content_type in (("gzip", "application/json"), ("identity", "text/html")):
        wire.encoding, wire.content_type = encoding, content_type
        with pytest.raises(CopilotBrokerUnavailable):
            wire.reader("core")
    wire.encoding, wire.content_type = "identity", "application/json"
    ticks = iter([0, 21])
    reader = CopilotBrokerReader(
        "https://broker.example.invalid", (wire.target,), wire.identity, now=lambda: next(ticks)
    )
    with pytest.raises(CopilotBrokerUnavailable):
        reader("core")


def test_no_identity_or_personal_fallback_on_configuration_failure(wire, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("personal/enterprise path must not be called")

    monkeypatch.setattr(github_monitor, "_enterprise_token", forbidden)
    monkeypatch.setattr(github_monitor, "get_copilot_org", forbidden)
    monkeypatch.setattr(github_monitor, "get_org_members_count", forbidden)
    monkeypatch.setattr(github_monitor, "get_enterprise_cloud_seats", forbidden)
    for orgs, price in [(["unknown"], 0), (["core", "CORE"], 0), (["core"], 21), (["core"], float("nan"))]:
        with pytest.raises(ValueError, match="^copilot_collection_scope_invalid$"):
            github_monitor.collect_github(
                "enterprise", orgs, {"enterprise_cloud_usd_per_seat": price}, copilot_reader=wire.reader
            )
    assert wire.calls == wire.audiences == []
    result = github_monitor.collect_github(
        "enterprise", ["core"], {"copilot_usd_per_seat": 19}, copilot_reader=wire.reader
    )
    assert result["copilot"]["seats_purchased"] == 7
    assert result["total_monthly_cost_usd"] == 133
    assert result["member_census_status"] == "not_collected_unpriced"


def test_selected_broker_failure_is_not_replaced_with_legacy_success(wire, monkeypatch):
    monkeypatch.setenv("ENTERPRISE_GITHUB_TOKEN", secrets.token_urlsafe(32))
    monkeypatch.setattr(github_monitor, "get_copilot_org", lambda _: pytest.fail("personal fallback"))
    wire.status = 503
    with pytest.raises(CopilotBrokerUnavailable):
        github_monitor.collect_github("enterprise", ["core"], {}, copilot_reader=wire.reader)


@pytest.mark.parametrize("price", [float("nan"), float("inf"), -1])
def test_invalid_copilot_price_fails_before_identity_or_network(wire, price):
    with pytest.raises(ValueError, match="^copilot_collection_price_invalid$"):
        github_monitor.collect_github(
            "enterprise", ["core"], {"copilot_usd_per_seat": price}, copilot_reader=wire.reader
        )
    assert wire.calls == wire.audiences == []


@pytest.mark.parametrize(
    "origin",
    [
        "http://broker.example.invalid",
        "https://broker.example.invalid/",
        "https://broker.example.invalid:443",
        "https://user@broker.example.invalid",
        "https://broker.example.invalid?target=x",
    ],
)
def test_untrusted_origin_variants_are_rejected_before_identity(origin):
    with pytest.raises(ValueError, match="^copilot_broker_configuration_invalid$"):
        CopilotBrokerReader(
            origin, (CopilotTarget(101, "core", "core-total", "0" * 64),), lambda _: pytest.fail("identity")
        )
