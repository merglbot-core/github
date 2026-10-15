"""Renewable Actions identity via public Google Auth, with no ADC discovery."""

import base64
import datetime as dt
import hmac
import json
import math
import os
import re
import stat
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

import requests
from google.auth import identity_pool
from google.auth.transport.requests import AuthorizedSession

from .copilot_broker import _unique

STS = "https://sts.googleapis.com/v1/token"
IAM = "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
JWT_TYPE = "urn:ietf:params:oauth:token-type:jwt"
BOUND = 65536
BEARER = re.compile(r"[A-Za-z0-9._~+/-]+=*")
SERVICE_ACCOUNT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com")


class CopilotIdentityUnavailable(RuntimeError):
    """No provider details, credentials, subject or paths in failures."""


def _json(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= BOUND:
        raise ValueError()
    return json.loads(raw, object_pairs_hook=_unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def _claims(value):
    if type(value) is not str or not 0 < len(value) <= 16384:
        raise ValueError()
    parts = value.split(".")
    if len(parts) != 3 or any(not re.fullmatch(r"[A-Za-z0-9_-]+", part) for part in parts):
        raise ValueError()
    return _json(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))


def _read_private(path):
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or not 0 < info.st_size <= BOUND
        ):
            raise ValueError()
        # The pinned first-party producer creates 0640. Tighten the opened,
        # owned single-link file before reading any authentication material.
        if stat.S_IMODE(info.st_mode) == 0o640:
            os.fchmod(descriptor, 0o600)
            info = os.fstat(descriptor)
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError()
        raw = bytearray()
        while True:
            chunk = os.read(descriptor, min(4096, BOUND + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > BOUND:
                raise ValueError()
        return _json(bytes(raw))
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _query(url):
    if type(url) is not str or not 0 < len(url) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in url):
        raise ValueError()
    parsed = urlsplit(url)
    pairs = parse_qsl(parsed.query, strict_parsing=True)
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError()
    return parsed, dict(pairs)


def _body(response, deadline):
    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
        raise ValueError()
    raw = bytearray()
    for chunk in response.iter_content(chunk_size=4096):
        if time.monotonic() >= deadline or len(raw) + len(chunk) > BOUND:
            raise ValueError()
        raw.extend(chunk)
    return bytes(raw)


class _FederationRequest:
    """Google Auth transport: exactly one Actions GET and one STS POST."""

    def __init__(self, source_url, audience, context, deadline, now):
        self.sequence = [("GET", source_url), ("POST", STS)]
        self.audience, self.context, self.deadline, self.now = audience, context, deadline, now

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        if not self.sequence or self.sequence.pop(0) != (method, url):
            raise ValueError()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or kwargs:
            raise ValueError()
        fields = dict(headers or {})
        fields["Accept-Encoding"] = "identity"
        # Separate fresh sessions prevent cross-endpoint cookie reuse.
        with requests.Session() as session:
            session.trust_env = False
            with session.request(
                method,
                url,
                data=body,
                headers=fields,
                stream=True,
                allow_redirects=False,
                verify=True,
                timeout=(min(5, remaining), min(10, remaining)),
            ) as response:
                raw = _body(response, self.deadline)
                status, response_headers = response.status_code, dict(response.headers)
        if method == "GET" and status == 200:
            assertion = _json(raw)
            claims = _claims(assertion["value"])
            if (
                claims.get("iss") != "https://token.actions.githubusercontent.com"
                or claims.get("aud") != self.audience
                or type(claims.get("exp")) is not int
                or not 30 < claims["exp"] - self.now() <= 3660
                or any(claims.get(key) != value for key, value in self.context.items())
            ):
                raise ValueError()
        if method == "POST" and status == 200:
            result = _json(raw)
            if (
                type(result) is not dict
                or result.get("token_type") != "Bearer"
                or type(result.get("access_token")) is not str
                or not 0 < len(result["access_token"]) <= 16384
                or not BEARER.fullmatch(result["access_token"])
                or type(result.get("expires_in")) is not int
                or not 240 < result["expires_in"] <= 3600
            ):
                raise ValueError()
        return SimpleNamespace(status=status, headers=response_headers, data=raw)


class ActionsWorkloadIdentity:
    """Explicit first-party external_account file; no key, login or user ADC.

    Google verifies the assertion signature/WIF authorization. The broker
    independently verifies the resulting Google signature and permitted subject.
    Local claim/context checks are parsing checks, never authorization.
    """

    def __init__(
        self,
        audience,
        service_account,
        subject,
        provider,
        context,
        credential_path,
        *,
        credential_account,
        now=time.time,
    ):
        if (
            type(service_account) is not str
            or not SERVICE_ACCOUNT.fullmatch(service_account)
            or type(credential_account) is not str
            or not SERVICE_ACCOUNT.fullmatch(credential_account)
            or credential_account == service_account
            or type(subject) is not str
            or not re.fullmatch(r"[1-9][0-9]{5,31}", subject)
            or type(provider) is not str
            or not re.fullmatch(
                r"projects/[1-9][0-9]*/locations/global/workloadIdentityPools/[a-z0-9-]+/providers/[a-z0-9-]+", provider
            )
            or type(audience) is not str
            or not audience.startswith("https://")
            or type(context) is not dict
            or set(context)
            != {"repository", "repository_id", "repository_owner_id", "ref", "event_name", "workflow_ref"}
            or any(type(value) is not str or not value for value in context.values())
            or type(credential_path) is not str
            or not Path(credential_path).is_absolute()
        ):
            raise ValueError("copilot_identity_configuration_invalid")
        self._audience, self._account, self._subject = audience, service_account, subject
        self._credential_account = credential_account
        self._provider, self._context, self._path = provider, dict(context), credential_path
        self._now, self._lock, self._value, self._expires, self._mono = now, threading.Lock(), None, 0, 0
        self._last_wall = float("-inf")
        self._margin = 300

    def _source(self):
        # The reviewed auth action creates this private file in GITHUB_WORKSPACE.
        # Do not accept GOOGLE_APPLICATION_CREDENTIALS, local ADC or other paths.
        expected = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": self._context["repository"],
            "GITHUB_REPOSITORY_ID": self._context["repository_id"],
            "GITHUB_REPOSITORY_OWNER_ID": self._context["repository_owner_id"],
            "GITHUB_REF": self._context["ref"],
            "GITHUB_EVENT_NAME": self._context["event_name"],
            "GITHUB_WORKFLOW_REF": self._context["workflow_ref"],
        }
        if any(os.environ.get(key) != value for key, value in expected.items()):
            raise ValueError()
        if not Path(os.environ["GITHUB_WORKSPACE"]).is_absolute():
            raise ValueError()
        workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
        path = Path(self._path)
        if path.parent.resolve(strict=True) != workspace or not re.fullmatch(
            r"gha-creds-[A-Za-z0-9_-]+\.json", path.name
        ):
            raise ValueError()
        value = _read_private(path)
        if type(value) is not dict or set(value) != {
            "type",
            "audience",
            "subject_token_type",
            "token_url",
            "credential_source",
            "service_account_impersonation_url",
        }:
            raise ValueError()
        if (
            value["type"] != "external_account"
            or value["audience"] != "//iam.googleapis.com/" + self._provider
            or value["subject_token_type"] != JWT_TYPE
            or value["token_url"] != STS
            or value["service_account_impersonation_url"] != IAM + self._credential_account + ":generateAccessToken"
        ):
            raise ValueError()
        source = value["credential_source"]
        if (
            type(source) is not dict
            or set(source) != {"url", "headers", "format"}
            or source["format"] != {"type": "json", "subject_token_field_name": "value"}
            or type(source["headers"]) is not dict
            or set(source["headers"]) != {"Authorization"}
        ):
            raise ValueError()
        original, query = _query(os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"])
        parsed, fields = _query(source["url"])
        if (
            original.scheme != "https"
            or original.port is not None
            or original.username
            or original.password
            or original.fragment
            or not re.fullmatch(r"[a-z0-9-]+\.actions\.githubusercontent\.com", original.hostname or "")
            or "/_apis/" not in original.path
            or not original.path.endswith("/idtoken")
            or "%" in original.path
            or ".." in original.path
            or parsed._replace(query="") != original._replace(query="")
            or fields != {**query, "audience": "https://iam.googleapis.com/" + self._provider}
        ):
            raise ValueError()
        header = source["headers"]["Authorization"]
        request_identity = os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]
        if (
            type(header) is not str
            or not header.startswith("Bearer ")
            or not 0 < len(request_identity) <= 16384
            or not BEARER.fullmatch(request_identity)
            or not hmac.compare_digest(header[7:], request_identity)
        ):
            raise ValueError()
        # Use the external identity directly for the fixed generateIdToken call;
        # do not activate a second generateAccessToken impersonation chain.
        selected = {
            key: value[key] for key in ("type", "audience", "subject_token_type", "token_url", "credential_source")
        }
        return (
            identity_pool.Credentials.from_info(selected, scopes=["https://www.googleapis.com/auth/cloud-platform"]),
            source["url"],
        )

    def __call__(self):
        if not self._lock.acquire(timeout=30):
            raise CopilotIdentityUnavailable("copilot_actions_identity_unavailable")
        try:
            current = self._now()
            if type(current) not in (int, float) or not math.isfinite(current):
                raise ValueError()
            if (
                current >= self._last_wall
                and self._value is not None
                and self._expires - current > self._margin
                and time.monotonic() < self._mono
            ):
                self._last_wall = current
                return self._value
            self._value = None
            source, source_url = self._source()
            deadline = time.monotonic() + 30
            request = _FederationRequest(
                source_url, "https://iam.googleapis.com/" + self._provider, self._context, deadline, self._now
            )
            source.refresh(request)
            expiry = source.expiry
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=dt.UTC)
            # Actions federation can issue a five-minute access token. The
            # pinned SDK refreshes at 225s; retain dispatch headroom without
            # incorrectly demanding an access token lasting over five minutes.
            if not 240 < expiry.timestamp() - self._now() <= 3660:
                raise ValueError()
            with AuthorizedSession(
                source, auth_request=request, max_refresh_attempts=0, refresh_status_codes=()
            ) as session:
                session.trust_env = False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError()
                with session.post(
                    IAM + self._account + ":generateIdToken",
                    json={"audience": self._audience, "includeEmail": True},
                    headers={"Accept-Encoding": "identity"},
                    allow_redirects=False,
                    stream=True,
                    verify=True,
                    max_allowed_time=remaining,
                    timeout=(min(5, remaining), min(10, remaining)),
                ) as response:
                    raw = _body(response, deadline)
                    if response.status_code != 200:
                        raise ValueError()
            result = _json(raw)
            if type(result) is not dict or set(result) != {"token"}:
                raise ValueError()
            claims = _claims(result["token"])
            now = self._now()
            if (
                claims.get("iss") not in ("https://accounts.google.com", "accounts.google.com")
                or claims.get("aud") != self._audience
                or claims.get("sub") != self._subject
                or claims.get("email") != self._account
                or claims.get("email_verified") is not True
                or type(claims.get("exp")) is not int
                or not 60 < claims["exp"] - now <= 3660
            ):
                raise ValueError()
            self._value, self._expires, self._last_wall = result["token"], claims["exp"], now
            self._margin = min(300, (claims["exp"] - now) / 2)
            self._mono = time.monotonic() + claims["exp"] - now - self._margin
            return self._value
        except Exception:
            self._value = None
            raise CopilotIdentityUnavailable("copilot_actions_identity_unavailable") from None
        finally:
            self._lock.release()
