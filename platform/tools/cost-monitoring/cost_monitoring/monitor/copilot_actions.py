"""Explicit CLI composition using public Google Auth and first-party Actions WIF."""

import json
import os
import re
import stat

from .copilot_broker import CopilotBrokerReader, CopilotTarget, _unique
from .copilot_identity import ActionsWorkloadIdentity

REPOSITORY = "merglbot-core/github"
REF = "refs/heads/main"
WORKFLOW = REPOSITORY + "/.github/workflows/cost-monitoring.yml@" + REF


def _fields(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError()


def _configuration(path):
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 16384:
            raise ValueError()
        raw = bytearray()
        while True:
            chunk = os.read(descriptor, min(4096, 16385 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > 16384:
                raise ValueError()
        return json.loads(raw, object_pairs_hook=_unique)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def load_actions_reader(path):
    """No network at construction, credential discovery or local ADC fallback."""
    try:
        value = _configuration(path)
        _fields(value, ("version", "origin", "identity", "targets"))
        if type(value["version"]) is not int or value["version"] != 1:
            raise ValueError()
        identity = value["identity"]
        _fields(
            identity,
            (
                "service_account",
                "subject",
                "provider",
                "repository",
                "repository_id",
                "repository_owner_id",
                "ref",
                "workflow_ref",
            ),
        )
        if (
            any(type(v) is not str for v in identity.values())
            or identity["repository"] != REPOSITORY
            or identity["ref"] != REF
            or identity["workflow_ref"] != WORKFLOW
            or any(
                not re.fullmatch(r"[1-9][0-9]{0,19}", identity[key]) for key in ("repository_id", "repository_owner_id")
            )
            or type(value["targets"]) is not list
            or not 1 <= len(value["targets"]) <= 32
        ):
            raise ValueError()
        targets = []
        for target in value["targets"]:
            _fields(target, ("organization_id", "organization_login", "grant_id", "target_sha256"))
            targets.append(CopilotTarget(**target))
        expected = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": REPOSITORY,
            "GITHUB_REF": REF,
            "GITHUB_WORKFLOW_REF": WORKFLOW,
            "GITHUB_REPOSITORY_ID": identity["repository_id"],
            "GITHUB_REPOSITORY_OWNER_ID": identity["repository_owner_id"],
        }
        if any(os.environ.get(key) != expected_value for key, expected_value in expected.items()) or os.environ.get(
            "GITHUB_EVENT_NAME"
        ) not in ("schedule", "workflow_dispatch"):
            raise ValueError()
        context = {
            "repository": REPOSITORY,
            "ref": REF,
            "workflow_ref": WORKFLOW,
            "event_name": os.environ["GITHUB_EVENT_NAME"],
            "repository_id": identity["repository_id"],
            "repository_owner_id": identity["repository_owner_id"],
        }
        provider = ActionsWorkloadIdentity(
            value["origin"],
            identity["service_account"],
            identity["subject"],
            identity["provider"],
            context,
            os.environ["GITHUB_ACCESS_WIF_CONFIGURATION"],
        )

        def obtain(audience):
            if audience != value["origin"]:
                raise ValueError("copilot_actions_audience_invalid")
            return provider()

        return CopilotBrokerReader(value["origin"], tuple(targets), obtain)
    except Exception:
        raise ValueError("copilot_actions_configuration_invalid") from None
