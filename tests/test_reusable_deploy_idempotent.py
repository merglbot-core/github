"""Idempotent image publication in the reusable Cloud Run deploy workflows (merglbot-core/github#909).

The `# BEGIN/END image-publish` block must be byte-identical in both workflows; it is run with bash
against a stub `docker`: tag present = adopt (no build/push), absent = build + push, anything else
fails. Skipped only without bash >= 4.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = [REPO_ROOT / ".github" / "workflows" / name
             for name in ("reusable-deploy-cloud-run.yml", "reusable-deploy-cloud-run-wif.yml")]
BASE = "europe-west1-docker.pkg.dev/p/containers/svc"
TAG = "a" * 40
LOCAL, PUSHED = "sha256:" + "b" * 64, "sha256:" + "c" * 64

DOCKER_STUB = """#!{python}
import os, sys
args = sys.argv[1:]
with open(os.environ["DOCKER_LOG"], "a", encoding="utf-8") as log:
    log.write(args[0] + "\\n")
mode, ref = os.environ["DOCKER_MODE"], os.environ["IMAGE_BASE"] + ":" + os.environ["IMAGE_TAG"]
pull = {{
    "present": (0, "done"),
    "absent": (1, f"Error response from daemon: manifest for {{ref}} not found: manifest unknown: x"),
    "absent-containerd": (1, f'failed to resolve reference "{{ref}}": {{ref}}: not found'),
    "denied": (1, "denied: Permission 'artifactregistry.repositories.downloadArtifacts' denied"),
    "unknown": (1, "Get https://x/v2/: dial tcp 10.0.0.1:443: i/o timeout"),
    "masked": (1, f"manifest for {{ref}} not found: unauthorized: authentication required"),
}}
if args[0] == "pull":
    rc, out = pull[mode.split("+")[0]]
    print(out, file=sys.stderr if rc else sys.stdout)
    sys.exit(rc)
if args[0] == "push":
    if "push-fails" in mode:
        sys.exit(1)
    print("tag: digest: " + ("" if "no-push-digest" in mode else "{pushed}") + " size: 1")
elif args[0] == "image" and "no-digest" not in mode:
    print("other/repo@{local}\\n" + os.environ["IMAGE_BASE"] + "@{local}")
"""


def extract_block(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^( *)# BEGIN image-publish\n(.*?)^\1# END image-publish$", text, re.S | re.M)
    if not match:
        raise AssertionError(f"image-publish block missing in {path.name}")
    return textwrap.dedent(match.group(0))


def find_bash() -> str | None:
    for cand in (shutil.which("bash"), "/opt/homebrew/bin/bash", "/usr/local/bin/bash", "/bin/bash"):
        if cand and os.path.exists(cand):
            out = subprocess.run([cand, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True)
            if out.stdout.strip().isdigit() and int(out.stdout) >= 4:
                return cand
    return None


BASH = find_bash()


class ImagePublishContractTests(unittest.TestCase):
    def test_block_is_identical_in_both_workflows_and_pipe_free(self) -> None:
        blocks = [extract_block(path) for path in WORKFLOWS]
        self.assertEqual(blocks[0], blocks[1])
        self.assertNotRegex(blocks[0], r"\|\s*(grep|tee|tail|head|sed|awk|cut)\b")
        self.assertNotIn("${{", blocks[0])

    def test_scans_and_deploy_use_the_digest_reference(self) -> None:
        wif, legacy = (path.read_text(encoding="utf-8") for path in reversed(WORKFLOWS))
        self.assertIn('echo "IMAGE=${IMAGE_REF}" >> "$GITHUB_ENV"', wif)
        self.assertEqual(2, wif.count("image-ref: ${{ env.IMAGE }}"))
        self.assertIn('echo "full_image=${IMAGE_REF}"', legacy)
        self.assertEqual(2, legacy.count("image-ref: ${{ steps.build.outputs.full_image }}"))


@unittest.skipUnless(BASH, "bash >= 4 is required to run the image-publish block")
class ImagePublishProbeTests(unittest.TestCase):
    def publish(self, mode: str) -> tuple[int, str, list[str]]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, body in (("docker", DOCKER_STUB.format(python=sys.executable, pushed=PUSHED,
                                                              local=LOCAL)),
                               ("sleep", "#!/bin/sh\nexit 0\n")):
                (root / name).write_text(body, encoding="utf-8")
                (root / name).chmod(0o755)
            script = ("set -euo pipefail\nbuild_image() { docker build -t \"$1\" .; }\n"
                      + extract_block(WORKFLOWS[0])
                      + '\nprintf "RESULT %s %s\\n" "$IMAGE_REF" "$IMAGE_ADOPTED"\n')
            env = {"PATH": f"{root}{os.pathsep}{os.environ['PATH']}", "DOCKER_MODE": mode,
                   "DOCKER_LOG": str(root / "log"), "IMAGE_BASE": BASE, "IMAGE_TAG": TAG}
            (root / "log").write_text("", encoding="utf-8")
            proc = subprocess.run([BASH, "-c", script], env=env, capture_output=True, text=True)
            calls = (root / "log").read_text(encoding="utf-8").split()
            return proc.returncode, proc.stdout + proc.stderr, calls

    def test_present_tag_is_adopted_without_build_or_push(self) -> None:
        rc, out, calls = self.publish("present")
        self.assertEqual(0, rc, out)
        self.assertIn(f"RESULT {BASE}@{LOCAL} true", out)
        self.assertNotIn("build", calls)
        self.assertNotIn("push", calls)

    def test_absent_tag_is_built_and_pushed(self) -> None:
        for mode in ("absent", "absent-containerd"):
            with self.subTest(mode):
                rc, out, calls = self.publish(mode)
                self.assertEqual(0, rc, out)
                self.assertIn(f"RESULT {BASE}@{PUSHED} false", out)
                self.assertEqual(["pull", "build", "push"], calls)
        rc, out, _ = self.publish("absent+no-push-digest")
        self.assertEqual(0, rc, out)
        self.assertIn(f"RESULT {BASE}@{LOCAL} false", out)

    def test_other_probe_errors_fail_without_building(self) -> None:
        for mode in ("denied", "unknown", "masked"):  # masked: auth error that also says not found
            with self.subTest(mode):
                rc, out, calls = self.publish(mode)
                self.assertNotEqual(0, rc, out)
                self.assertEqual(["pull"], calls)
                self.assertIn("not guessing", out)

    def test_push_failure_and_unresolvable_digest_fail(self) -> None:
        for mode in ("absent+push-fails", "present+no-digest"):
            with self.subTest(mode):
                rc, out, _ = self.publish(mode)
                self.assertNotEqual(0, rc, out)
                self.assertNotIn("RESULT", out)


if __name__ == "__main__":
    unittest.main()
