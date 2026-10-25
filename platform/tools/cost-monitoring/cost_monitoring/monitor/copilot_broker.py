"""Aggregate-only broker client; trusted workload identity is injected explicitly."""

import json
import math
import re
import time
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import urlsplit

import requests


class CopilotBrokerUnavailable(RuntimeError):
    """Safe enumerated failure, never an identity, payload or provider exception."""


@dataclass(frozen=True)
class CopilotTarget:
    organization_id: int
    organization_login: str
    grant_id: str
    target_sha256: str

    def __post_init__(self):
        if (
            type(self.organization_id) is not int
            or self.organization_id <= 0
            or type(self.organization_login) is not str
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?", self.organization_login)
            or "--" in self.organization_login
            or type(self.grant_id) is not str
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", self.grant_id)
            or type(self.target_sha256) is not str
            or not re.fullmatch(r"[a-f0-9]{64}", self.target_sha256)
        ):
            raise ValueError("copilot_target_invalid")


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


class CopilotBrokerReader:
    """No credential discovery, direct GitHub access, personal fallback or retries.

    identity(audience) must be the caller's bounded, reviewed workload identity
    provider. It is called for each request so the provider can refresh it.
    The trusted targets must be accepted deployment configuration, not values
    copied from the broker response, a tool argument or a discovery result.
    """

    def __init__(self, origin, targets, identity, *, now=time.monotonic):
        try:
            parsed = urlsplit(origin)
            if (
                type(origin) is not str
                or parsed.scheme != "https"
                or not parsed.hostname
                or not re.fullmatch(r"[a-z0-9.-]+", parsed.hostname)
                or origin != "https://" + parsed.hostname
                or parsed.port is not None
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
                or type(targets) is not tuple
                or not 1 <= len(targets) <= 32
                or any(type(target) is not CopilotTarget for target in targets)
                or len({target.organization_id for target in targets}) != len(targets)
                or len({target.organization_login for target in targets}) != len(targets)
                or len({target.grant_id for target in targets}) != len(targets)
                or not callable(identity)
                or not callable(now)
            ):
                raise ValueError()
        except Exception:
            raise ValueError("copilot_broker_configuration_invalid") from None
        self._origin, self._identity, self._now = origin, identity, now
        self.targets = MappingProxyType({target.organization_login: target for target in targets})

    def validate_collection(self, orgs, enterprise_price):
        """Refuse unsupported enterprise operations before acquiring any identity."""
        if (
            type(orgs) is not list
            or not orgs
            or any(type(org) is not str or org.lower() not in self.targets for org in orgs)
            or len({org.lower() for org in orgs}) != len(orgs)
            or not math.isfinite(enterprise_price)
            or enterprise_price != 0
        ):
            raise ValueError("copilot_collection_scope_invalid")

    def __call__(self, organization):
        if type(organization) is not str or organization.lower() not in self.targets:
            raise ValueError("copilot_collection_scope_invalid")
        target = self.targets[organization.lower()]
        try:
            identity = self._identity(self._origin)
            if type(identity) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,16384}", identity):
                raise ValueError()
            started = self._now()
            if type(started) not in (int, float) or not math.isfinite(started):
                raise ValueError()
            with requests.Session() as session:
                session.trust_env = False
                with session.post(
                    self._origin + "/v1/copilot-billing",
                    json={"grant_id": target.grant_id, "target_sha256": target.target_sha256},
                    headers={"Authorization": "Bearer " + identity, "Accept": "application/json"},
                    timeout=(5, 10),
                    allow_redirects=False,
                    stream=True,
                    verify=True,
                ) as response:
                    if (
                        response.status_code != 200
                        or response.headers.get("Content-Encoding", "identity").lower() != "identity"
                        or response.headers.get("Content-Type", "").split(";", 1)[0].lower() != "application/json"
                    ):
                        raise ValueError()
                    body = bytearray()
                    for chunk in response.iter_content(chunk_size=4096):
                        current = self._now()
                        if (
                            type(current) not in (int, float)
                            or not math.isfinite(current)
                            or not 0 <= current - started < 20
                            or len(body) + len(chunk) > 4096
                        ):
                            raise ValueError()
                        body.extend(chunk)
            payload = json.loads(body, object_pairs_hook=_unique, parse_constant=lambda _: None)
            if (
                type(payload) is not dict
                or set(payload) != {"organization", "seat_breakdown"}
                or type(payload["organization"]) is not dict
                or set(payload["organization"]) != {"id", "login"}
                or type(payload["organization"]["id"]) is not int
                or payload["organization"]["id"] != target.organization_id
                or payload["organization"]["login"] != target.organization_login
                or type(payload["seat_breakdown"]) is not dict
                or set(payload["seat_breakdown"]) != {"total"}
                or type(payload["seat_breakdown"]["total"]) is not int
                or not 0 <= payload["seat_breakdown"]["total"] <= 2**31 - 1
            ):
                raise ValueError()
            total = payload["seat_breakdown"]["total"]
            return {"seats_assigned": total, "seats_purchased": total}
        except Exception:
            raise CopilotBrokerUnavailable("copilot_broker_read_unavailable") from None
