import base64
import datetime as dt
import json
import os
import secrets
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
import requests
from google.auth import _helpers

from cost_monitoring.monitor.copilot_identity import IAM, STS, ActionsWorkloadIdentity, CopilotIdentityUnavailable

ACCOUNT = "copilot-reader@runtime-project.iam.gserviceaccount.com"
FILE_ACCOUNT = "billing-reader@runtime-project.iam.gserviceaccount.com"
SUBJECT = "123456789"
PROVIDER = "projects/123456/locations/global/workloadIdentityPools/actions/providers/github"
ORIGIN = "https://broker.example.invalid"
REQUEST_URL = "https://vstoken.actions.githubusercontent.com/fixture/_apis/actions/idtoken?api-version=2.0"
CONTEXT = {
    "repository": "merglbot-core/github",
    "repository_id": "42",
    "repository_owner_id": "101",
    "ref": "refs/heads/main",
    "event_name": "schedule",
    "workflow_ref": "merglbot-core/github/.github/workflows/cost-monitoring.yml@refs/heads/main",
}


def assertion(claims):
    return ".".join(
        base64.urlsafe_b64encode(value).decode().rstrip("=")
        for value in (b"{}", json.dumps(claims).encode(), secrets.token_bytes(32))
    )


@pytest.fixture
def identity_wire(tmp_path, monkeypatch):
    class Fixture:
        def __init__(self):
            self.clock = [int(time.time())]
            self.calls = []
            self.request_identity, self.federated = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self.failure = None
            self.sts_lifetime, self.google_lifetime = 300, 600
            self.broker_response, self.latest_google = None, None
            self.claim_changes, self.google_changes = {}, {}
            self.file = tmp_path / "gha-creds-fixture.json"
            self.source_url = REQUEST_URL + "&" + urlencode({"audience": "https://iam.googleapis.com/" + PROVIDER})
            self.value = {
                "type": "external_account",
                "audience": "//iam.googleapis.com/" + PROVIDER,
                "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                "token_url": STS,
                "credential_source": {
                    "url": self.source_url,
                    "headers": {"Authorization": "Bearer " + self.request_identity},
                    "format": {"type": "json", "subject_token_field_name": "value"},
                },
                "service_account_impersonation_url": IAM + FILE_ACCOUNT + ":generateAccessToken",
            }

        def write(self):
            self.file.write_text(json.dumps(self.value))
            self.file.chmod(0o640)

        def provider(self):
            return ActionsWorkloadIdentity(
                ORIGIN,
                ACCOUNT,
                SUBJECT,
                PROVIDER,
                CONTEXT,
                str(self.file),
                credential_account=FILE_ACCOUNT,
                now=lambda: self.clock[0],
            )

    f = Fixture()
    f.write()
    environment = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_WORKSPACE": str(tmp_path),
        "GITHUB_REPOSITORY": CONTEXT["repository"],
        "GITHUB_REPOSITORY_ID": CONTEXT["repository_id"],
        "GITHUB_REPOSITORY_OWNER_ID": CONTEXT["repository_owner_id"],
        "GITHUB_REF": CONTEXT["ref"],
        "GITHUB_EVENT_NAME": CONTEXT["event_name"],
        "GITHUB_WORKFLOW_REF": CONTEXT["workflow_ref"],
        "ACTIONS_ID_TOKEN_REQUEST_URL": REQUEST_URL,
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": f.request_identity,
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(_helpers, "utcnow", lambda: dt.datetime.fromtimestamp(f.clock[0], dt.UTC).replace(tzinfo=None))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.reply()

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            f.calls[-1]["body"] = json.loads(raw) if f.calls[-1]["route"] == "iam" else raw.decode()
            self.reply()

        def reply(self):
            route = f.calls[-1]["route"]
            claims = {
                "iss": "https://token.actions.githubusercontent.com",
                "aud": "https://iam.googleapis.com/" + PROVIDER,
                "exp": f.clock[0] + 300,
                **CONTEXT,
                **f.claim_changes,
            }
            google = {
                "iss": "https://accounts.google.com",
                "aud": ORIGIN,
                "sub": SUBJECT,
                "email": ACCOUNT,
                "email_verified": True,
                "exp": f.clock[0] + f.google_lifetime,
                **f.google_changes,
            }
            values = {
                "actions": {"value": assertion(claims)},
                "sts": {"access_token": f.federated, "token_type": "Bearer", "expires_in": f.sts_lifetime},
                "iam": {"token": assertion(google)},
            }
            if route == "iam":
                f.latest_google = values["iam"]["token"]
            if f.broker_response is not None:
                values["broker"] = f.broker_response
            payload = json.dumps(values[route] if route != f.failure else {"error": "PRIVATE_CONTEXT"}).encode()
            self.send_response(403 if route == f.failure else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = requests.adapters.HTTPAdapter.send

    def send(adapter, request, **kwargs):
        routes = {f.source_url: "actions", STS: "sts", IAM + ACCOUNT + ":generateIdToken": "iam"}
        if f.broker_response is not None:
            routes[ORIGIN + "/v1/copilot-billing"] = "broker"
        assert request.url in routes
        route = routes[request.url]
        expected = (
            f.request_identity
            if route == "actions"
            else f.federated if route == "iam" else f.latest_google if route == "broker" else None
        )
        f.calls.append(
            {
                "route": route,
                "method": request.method,
                "url": request.url,
                "verified_tls": kwargs["verify"],
                "proxies": kwargs["proxies"],
                "auth_bound": (
                    request.headers.get("Authorization") == "Bearer " + expected
                    if expected is not None
                    else "Authorization" not in request.headers
                ),
            }
        )
        request.url = f"http://127.0.0.1:{server.server_port}/fixture"
        return original(adapter, request, **kwargs)

    try:
        with patch.object(requests.adapters.HTTPAdapter, "send", send):
            yield f
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.parametrize("google_lifetime", [300, 600, 3600])
def test_actual_google_auth_chain_caches_and_renews_without_adc(identity_wire, monkeypatch, google_lifetime):
    f = identity_wire
    f.google_lifetime = google_lifetime
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/forbidden/personal.json")
    monkeypatch.setenv("ENTERPRISE_GITHUB_TOKEN", secrets.token_urlsafe(32))
    provider = f.provider()
    first = provider()
    assert f.file.stat().st_mode & 0o777 == 0o600
    assert provider() == first
    assert [call["route"] for call in f.calls] == ["actions", "sts", "iam"]
    assert all(call["verified_tls"] and call["proxies"] == {} and call["auth_bound"] for call in f.calls)
    assert f.calls[-1]["body"] == {"audience": ORIGIN, "includeEmail": True}
    assert "generateAccessToken" not in str(f.calls)
    f.clock[0] += int(google_lifetime - min(300, google_lifetime / 2) + 1)
    assert provider() != first
    assert [call["route"] for call in f.calls] == ["actions", "sts", "iam"] * 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("repository", "untrusted/repo"),
        ("repository_id", "43"),
        ("repository_owner_id", "102"),
        ("ref", "refs/pull/1/merge"),
        ("workflow_ref", "other-workflow"),
        ("aud", "other-audience"),
    ],
)
def test_changed_signed_context_is_refused_before_sts(identity_wire, field, value):
    f = identity_wire
    f.claim_changes[field] = value
    with pytest.raises(CopilotIdentityUnavailable, match="^copilot_actions_identity_unavailable$"):
        f.provider()()
    assert [call["route"] for call in f.calls] == ["actions"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("aud", "other-audience"),
        ("sub", "987654321"),
        ("email", "other@example.invalid"),
        ("email_verified", False),
        ("exp", True),
    ],
)
def test_changed_google_target_never_leaves_provider(identity_wire, field, value):
    f = identity_wire
    f.google_changes[field] = value
    with pytest.raises(CopilotIdentityUnavailable, match="^copilot_actions_identity_unavailable$"):
        f.provider()()
    assert len(f.calls) == 3


@pytest.mark.parametrize("route", ["actions", "sts", "iam"])
def test_provider_errors_are_safe_and_never_retry(identity_wire, route):
    f = identity_wire
    f.failure = route
    with pytest.raises(CopilotIdentityUnavailable, match="^copilot_actions_identity_unavailable$"):
        f.provider()()
    assert [call["route"] for call in f.calls] == ["actions", "sts", "iam"][
        : ["actions", "sts", "iam"].index(route) + 1
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("type", "service_account"),
        ("audience", "other"),
        ("token_url", "https://elsewhere.example.invalid/token"),
        ("service_account_impersonation_url", IAM + "other:generateAccessToken"),
    ],
)
def test_other_credential_types_and_endpoints_fail_before_network(identity_wire, field, value):
    f = identity_wire
    f.value[field] = value
    f.write()
    with pytest.raises(CopilotIdentityUnavailable):
        f.provider()()
    assert f.calls == []


def test_public_or_symlinked_credential_file_is_refused(identity_wire, tmp_path):
    f = identity_wire
    f.file.chmod(0o644)
    with pytest.raises(CopilotIdentityUnavailable):
        f.provider()()
    f.file.unlink()
    target = tmp_path / "target"
    target.write_text(json.dumps(f.value))
    target.chmod(0o600)
    f.file.symlink_to(target)
    with pytest.raises(CopilotIdentityUnavailable):
        f.provider()()
    assert f.calls == []


def test_first_party_file_is_tightened_before_read_without_replacing_billing_identity(identity_wire, monkeypatch):
    f = identity_wire
    original_bytes = f.file.read_bytes()
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(f.file))
    original = os.read

    def read(descriptor, count):
        info = os.fstat(descriptor)
        if info.st_ino == f.file.stat().st_ino:
            assert stat.S_IMODE(info.st_mode) == 0o600
        return original(descriptor, count)

    monkeypatch.setattr(os, "read", read)
    assert f.provider()()
    assert f.file.read_bytes() == original_bytes
    assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == str(f.file)
    assert f.value["service_account_impersonation_url"] == IAM + FILE_ACCOUNT + ":generateAccessToken"
    assert f.calls[-1]["url"] == IAM + ACCOUNT + ":generateIdToken"
    assert "generateAccessToken" not in str(f.calls)


def test_hardlinked_producer_file_is_not_read_or_chmodded(identity_wire, tmp_path):
    f = identity_wire
    os.link(f.file, tmp_path / "second-link")
    with pytest.raises(CopilotIdentityUnavailable):
        f.provider()()
    assert stat.S_IMODE(f.file.stat().st_mode) == 0o640
    assert f.calls == []


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o400])
def test_other_producer_modes_are_not_repaired_or_read(identity_wire, mode):
    f = identity_wire
    f.file.chmod(mode)
    with pytest.raises(CopilotIdentityUnavailable):
        f.provider()()
    assert stat.S_IMODE(f.file.stat().st_mode) == mode
    assert f.calls == []
