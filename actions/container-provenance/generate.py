"""Build bound JSON statements from runner inputs without shell interpolation."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid


def required(environment, name):
    value = environment.get(name, "")
    if not value:
        raise ValueError("missing " + name)
    return value


def statements(environment):
    candidate = required(environment, "CANDIDATE_DIGEST")
    platform = environment.get("PLATFORM_DIGEST", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", candidate):
        raise ValueError("invalid candidate digest")
    if platform and not re.fullmatch(r"sha256:[0-9a-f]{64}", platform):
        raise ValueError("invalid platform digest")
    repository = required(environment, "GITHUB_REPOSITORY")
    commit = required(environment, "GITHUB_SHA")
    run_id = required(environment, "GITHUB_RUN_ID")
    attempt = required(environment, "GITHUB_RUN_ATTEMPT")
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9_.-]+", repository):
        raise ValueError("invalid caller repository")
    if repository.split("/", 1)[1] in {".", ".."}:
        raise ValueError("invalid caller repository")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("invalid caller commit")
    if not re.fullmatch(r"[1-9][0-9]*", run_id) or not re.fullmatch(r"[1-9][0-9]*", attempt):
        raise ValueError("invalid caller run identity")
    ref = required(environment, "GITHUB_REF")
    workflow = required(environment, "GITHUB_WORKFLOW")
    actor = required(environment, "GITHUB_ACTOR")
    image = required(environment, "IMAGE_REGISTRY") + "/" + required(environment, "IMAGE_NAME")
    document = {
        "_type": "https://in-toto.io/Statement/v0.1",
        "predicateType": "https://slsa.dev/provenance/v0.2",
        "subject": [{"name": image, "digest": {"sha256": candidate.removeprefix("sha256:")}}],
        "predicate": {
            "builder": {"id": f"https://github.com/{repository}/actions/runs/{run_id}"},
            # Preserve the existing declared string; this does not certify a generator or level.
            "buildType": "https://github.com/slsa-framework/slsa-github-generator/container@v1",
            "invocation": {
                "configSource": {
                    "uri": f"git+https://github.com/{repository}@{ref}",
                    "digest": {"sha1": commit},
                    "entryPoint": workflow,
                },
                "parameters": {},
                "environment": {
                    "github_actor": actor, "github_sha": commit,
                    "github_repository": repository, "github_ref": ref, "github_run_id": run_id,
                },
            },
            "buildConfig": {
                "dockerfile": environment.get("BUILD_DOCKERFILE", "Dockerfile"),
                "context": environment.get("BUILD_CONTEXT", "."),
            },
            "metadata": {
                "buildInvocationId": run_id + "-" + attempt,
                "buildStartedOn": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "completeness": {"parameters": True, "environment": True, "materials": True},
                "reproducible": False,
            },
            "materials": [{"uri": f"git+https://github.com/{repository}", "digest": {"sha1": commit}}],
        },
    }
    projection = None
    if platform:
        document["subject"].append({"name": image, "digest": {"sha256": platform.removeprefix("sha256:")}})
        projection = copy.deepcopy(document)
        projection["subject"] = [copy.deepcopy(document["subject"][-1])]
    return document, projection


def generate(environment):
    document, projection = statements(environment)
    output = Path(required(environment, "GITHUB_OUTPUT"))
    runner_temp = Path(required(environment, "RUNNER_TEMP"))
    if not runner_temp.is_dir():
        raise ValueError("runner temporary directory unavailable")
    full_json = json.dumps(document, indent=2)
    platform_json = json.dumps(projection, separators=(",", ":")) if projection else ""
    delimiter = "PROVENANCE_EOF_" + uuid.uuid4().hex
    if delimiter in full_json.splitlines():
        raise ValueError("output delimiter collision")
    # Never upload a caller-controlled stale file from the checkout directory.
    directory = Path(tempfile.mkdtemp(prefix="container-provenance-", dir=runner_temp))
    full_path = directory / "provenance.json"
    full_path.write_text(full_json + "\n", encoding="utf-8")
    platform_path = directory / "platform-provenance.json"
    if projection:
        platform_path.write_text(platform_json + "\n", encoding="utf-8")
    lines = [f"provenance<<{delimiter}\n{full_json}\n{delimiter}\n",
             f"provenance_path={full_path}\n"]
    if projection:
        lines.extend([f"platform_provenance={platform_json}\n",
                      f"platform_provenance_path={platform_path}\n"])
    # Only publish outputs after validation and both statement writes succeed.
    with output.open("a", encoding="utf-8") as target:
        target.write("".join(lines))


if __name__ == "__main__":
    try:
        generate(os.environ)
    except (ValueError, OSError):
        # Inputs may contain untrusted metadata; never echo them into logs.
        print("::error::Cannot generate validated container provenance", file=sys.stderr)
        sys.exit(1)
