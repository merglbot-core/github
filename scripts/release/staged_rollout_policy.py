#!/usr/bin/env python3
"""Fail-closed, offline traffic and revision checks for a staged Cloud Run rollout.

This module performs no API calls or mutations. Callers must collect fresh service
and revision documents immediately before promotion, under their deployment lock.
It does not replace the workflow's CI, image-provenance or authority checks.
"""
from __future__ import annotations

import re
import argparse
import json
from pathlib import Path


class Refused(ValueError):
    """The supplied evidence does not authorize the proposed traffic change."""


def require(value, message):
    if not value:
        raise Refused(message)


def verify_access(service, iam, requested):
    """Check existing anonymous invocation without changing service-wide IAM.

    Conditional or nonstandard public grants need a separately reviewed access
    contract. An absent bindings list is a valid empty get-iam-policy response.
    """
    require(requested in ("true", "false"), "invalid requested access state")
    require(isinstance(iam, dict), "invalid IAM policy")
    bindings = iam.get("bindings", [])
    require(isinstance(bindings, list), "invalid IAM bindings")
    disabled = service.get("metadata", {}).get("annotations", {}).get(
        "run.googleapis.com/invoker-iam-disabled", "false")
    require(disabled in ("true", "false"), "unknown invoker IAM check state")
    public = disabled == "true"
    for binding in bindings:
        require(isinstance(binding, dict), "invalid IAM binding")
        members = binding.get("members")
        require(isinstance(members, list) and all(isinstance(m, str) for m in members)
                and isinstance(binding.get("role"), str), "invalid IAM grant")
        if "allUsers" in members:
            require(binding["role"] == "roles/run.invoker" and not binding.get("condition"),
                    "ambiguous anonymous access grant")
            public = True
    require(public == (requested == "true"),
            "existing access differs; separate authorized IAM change required")
    return {"anonymous_invocation": public, "iam_mutation": False}


def serving_state(service):
    """Resolve LATEST from status, never preserve a movable alias as rollback."""
    metadata = service.get("metadata", {})
    status = service.get("status", {})
    require(metadata.get("name") and metadata.get("uid"), "missing service identity")
    require(metadata.get("generation") == status.get("observedGeneration")
            and metadata.get("generation") is not None, "unobserved service generation")
    conditions = {c.get("type"): c.get("status") for c in status.get("conditions", [])}
    require(conditions.get("RoutesReady") == "True", "serving routes not Ready")
    traffic = status.get("traffic")
    require(isinstance(traffic, list) and traffic, "missing resolved traffic")
    split, tags = {}, {}
    for target in traffic:
        revision = target.get("revisionName")
        require(isinstance(revision, str) and re.fullmatch(r"[a-z][a-z0-9-]*", revision),
                "unresolved revision identity")
        percent = target.get("percent", 0)
        require(type(percent) is int and 0 <= percent <= 100, "invalid traffic percentage")
        if percent:
            split[revision] = split.get(revision, 0) + percent
        if "tag" in target:
            tag = target["tag"]
            require(isinstance(tag, str) and tag and tag not in tags, "ambiguous traffic tag")
            tags[tag] = revision
    require(sum(split.values()) == 100, "traffic is not fully resolved")
    annotations = metadata.get("annotations", {})
    return {
        "service": metadata["name"], "uid": metadata["uid"],
        "namespace": metadata.get("namespace"),
        "split": dict(sorted(split.items())), "tags": dict(sorted(tags.items())),
        "ingress": annotations.get("run.googleapis.com/ingress"),
        "default_url_disabled": annotations.get("run.googleapis.com/default-url-disabled"),
    }


def promotion_plan(before, current, revision, *, expected_revision, expected_image,
                   health_path, port=8080):
    """Require exact-image HTTP startup success and unchanged serving traffic.

    HTTP startup checks exercise the container without opening a private ingress.
    They prove the configured startup endpoint, not an end-to-end business flow.
    No LATEST-based promotion or automatic rollback of foreign traffic is emitted.
    """
    original, live = serving_state(before), serving_state(current)
    require(original == live, "serving identity, traffic, tags or ingress changed")
    require(isinstance(expected_image, str)
            and re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", expected_image),
            "expected image must be pinned to its digest")
    require(isinstance(health_path, str) and re.fullmatch(r"/[A-Za-z0-9_./-]+", health_path),
            "invalid health path")
    require(type(port) is int and 1 <= port <= 65535, "invalid health port")
    metadata, status = revision.get("metadata", {}), revision.get("status", {})
    require(metadata.get("name") == expected_revision, "foreign revision")
    require(metadata.get("labels", {}).get("serving.knative.dev/service") == live["service"],
            "foreign service revision")
    require(metadata.get("namespace") == live["namespace"] and live["namespace"],
            "foreign or missing project namespace")
    require(current.get("status", {}).get("latestCreatedRevisionName") == expected_revision,
            "candidate superseded")
    require(current.get("status", {}).get("latestReadyRevisionName") == expected_revision,
            "candidate is not latest Ready")
    require(metadata.get("generation") == status.get("observedGeneration")
            and metadata.get("generation") is not None, "unobserved revision generation")
    require(status.get("imageDigest") == expected_image, "revision image mismatch")
    conditions = {c.get("type"): c.get("status") for c in status.get("conditions", [])}
    require(all(conditions.get(key) == "True" for key in ("Ready", "ContainerHealthy")),
            "candidate startup not successful")
    containers = revision.get("spec", {}).get("containers", [])
    require(len(containers) == 1, "multi-container health requires a separate contract")
    probe = containers[0].get("startupProbe", {})
    require(probe.get("httpGet") == {"path": health_path, "port": port},
            "HTTP startup endpoint mismatch")
    require("tcpSocket" not in probe and "grpc" not in probe, "ambiguous startup probe")
    expected_probe = {"initialDelaySeconds": 0, "failureThreshold": 24,
                      "timeoutSeconds": 5, "periodSeconds": 10}
    require(all(type(probe.get(field, 0 if field == "initialDelaySeconds" else None)) is int
                and probe.get(field, 0 if field == "initialDelaySeconds" else None) == value
                for field, value in expected_probe.items()), "startup timing contract mismatch")
    return {
        "revision": expected_revision, "image": expected_image,
        "promote_argument": f"{expected_revision}=100",
        "rollback_argument": ",".join(f"{name}={percent}" for name, percent in original["split"].items()),
        "preserve_tags": original["tags"],
        "health_scope": "exact_revision_http_startup_only",
        "requires_fresh_readback_and_deployment_lock": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--iam-policy", type=Path)
    parser.add_argument("--requested-public", choices=("true", "false"))
    parser.add_argument("--current", type=Path)
    parser.add_argument("--revision", type=Path)
    parser.add_argument("--expected-revision")
    parser.add_argument("--expected-image")
    parser.add_argument("--health-path")
    args = parser.parse_args()
    try:
        if args.iam_policy:
            print(json.dumps(verify_access(json.loads(args.before.read_text()),
                                          json.loads(args.iam_policy.read_text()),
                                          args.requested_public), sort_keys=True))
            return
        if args.snapshot:
            print(json.dumps(serving_state(json.loads(args.before.read_text())), sort_keys=True))
            return
        require(all((args.current, args.revision, args.expected_revision,
                     args.expected_image, args.health_path)), "incomplete promotion arguments")
        plan = promotion_plan(*(json.loads(p.read_text()) for p in
                                (args.before, args.current, args.revision)),
                              expected_revision=args.expected_revision,
                              expected_image=args.expected_image, health_path=args.health_path)
    except (Refused, ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, f"Staged rollout refused: {error}\n")
    print(json.dumps(plan, sort_keys=True))


if __name__ == "__main__":
    main()
