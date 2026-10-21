"""Synthetic contract tests; no Cloud Run access or production acceptance."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "scripts/release/staged_rollout_policy.py"
SPEC = importlib.util.spec_from_file_location("staged_rollout_policy", MODULE)
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)
IMAGE = "europe-west1-docker.pkg.dev/project/images/service@sha256:" + "a" * 64


def evidence():
    before = {
        "metadata": {"name": "service", "uid": "service-uid", "namespace": "123", "generation": 3,
                     "annotations": {"run.googleapis.com/ingress": "internal-and-cloud-load-balancing"}},
        "status": {"observedGeneration": 3, "conditions": [{"type": "Ready", "status": "True"}, {"type": "RoutesReady", "status": "True"}],
                   "traffic": [{"revisionName": "service-old", "percent": 100, "latestRevision": True}]},
    }
    current = copy.deepcopy(before)
    current["metadata"]["generation"] = 4
    current["status"].update(observedGeneration=4, latestCreatedRevisionName="service-new",
                             latestReadyRevisionName="service-new")
    current["status"]["traffic"][0].pop("latestRevision")
    revision = {
        "metadata": {"name": "service-new", "namespace": "123", "generation": 1,
                     "labels": {"serving.knative.dev/service": "service"}},
        "status": {"observedGeneration": 1, "imageDigest": IMAGE,
                   "conditions": [{"type": name, "status": "True"} for name in ("Ready", "ContainerHealthy")]},
        "spec": {"containers": [{"startupProbe": {"httpGet": {"path": "/healthz", "port": 8080},
                   "failureThreshold": 24, "timeoutSeconds": 5, "periodSeconds": 10}}]},
    }
    return before, current, revision


class StagedRolloutPolicyTests(unittest.TestCase):
    def plan(self, docs):
        return policy.promotion_plan(*docs, expected_revision="service-new", expected_image=IMAGE,
                                     health_path="/healthz")

    def test_private_ingress_http_probe_and_pinned_rollback(self):
        plan = self.plan(evidence())
        self.assertEqual(plan["promote_argument"], "service-new=100")
        self.assertEqual(plan["rollback_argument"], "service-old=100")
        self.assertNotIn("latest", plan["rollback_argument"])

    def test_failed_latest_revision_does_not_prevent_repair(self):
        docs = evidence()
        docs[0]["status"]["conditions"][0]["status"] = "False"
        docs[0]["status"]["latestCreatedRevisionName"] = "service-failed"
        self.assertEqual(self.plan(docs)["promote_argument"], "service-new=100")

    def test_unready_routes_cannot_authorize_repair(self):
        docs = evidence()
        docs[0]["status"]["conditions"][1]["status"] = "False"
        with self.assertRaisesRegex(policy.Refused, "routes"):
            self.plan(docs)

    def test_existing_split_and_tags_are_preserved(self):
        docs = evidence()
        for service in docs[:2]:
            service["status"]["traffic"] = [
                {"revisionName": "service-old", "percent": 80},
                {"revisionName": "service-older", "percent": 20},
                {"revisionName": "service-old", "tag": "rollback"},
            ]
        plan = self.plan(docs)
        self.assertEqual(plan["rollback_argument"], "service-old=80,service-older=20")
        self.assertEqual(plan["preserve_tags"], {"rollback": "service-old"})

    def test_rejects_traffic_shift_before_health(self):
        docs = evidence()
        docs[1]["status"]["traffic"][0]["revisionName"] = "service-new"
        with self.assertRaisesRegex(policy.Refused, "traffic"):
            self.plan(docs)

    def test_rejects_ingress_or_tag_change(self):
        for mutation in ("ingress", "tag"):
            with self.subTest(mutation=mutation):
                docs = evidence()
                if mutation == "ingress":
                    docs[1]["metadata"]["annotations"]["run.googleapis.com/ingress"] = "all"
                else:
                    docs[1]["status"]["traffic"][0]["tag"] = "foreign"
                with self.assertRaises(policy.Refused):
                    self.plan(docs)

    def test_rejects_tcp_probe_even_when_ready(self):
        docs = evidence()
        docs[2]["spec"]["containers"][0]["startupProbe"] = {"tcpSocket": {"port": 8080}}
        with self.assertRaisesRegex(policy.Refused, "HTTP"):
            self.plan(docs)

    def test_rejects_foreign_probe_timing(self):
        for key, value in (("periodSeconds", 240), ("timeoutSeconds", True),
                           ("failureThreshold", 1), ("initialDelaySeconds", 5)):
            docs = evidence()
            docs[2]["spec"]["containers"][0]["startupProbe"][key] = value
            with self.assertRaisesRegex(policy.Refused, "timing"):
                self.plan(docs)

    def test_rejects_failed_or_missing_health(self):
        for state in ("False", "Unknown", None):
            docs = evidence()
            docs[2]["status"]["conditions"][1]["status"] = state
            with self.assertRaisesRegex(policy.Refused, "startup"):
                self.plan(docs)

    def test_rejects_different_image(self):
        docs = evidence()
        docs[2]["status"]["imageDigest"] = IMAGE.replace("a" * 64, "b" * 64)
        with self.assertRaisesRegex(policy.Refused, "image"):
            self.plan(docs)

    def test_rejects_foreign_or_superseded_revision(self):
        for field in ("name", "namespace", "superseded"):
            docs = evidence()
            if field == "superseded":
                docs[1]["status"]["latestCreatedRevisionName"] = "service-later"
            else:
                docs[2]["metadata"][field] = "foreign"
            with self.assertRaises(policy.Refused):
                self.plan(docs)

    def test_rejects_unobserved_generations(self):
        for index in range(3):
            docs = evidence()
            docs[index]["status"]["observedGeneration"] = 0
            with self.assertRaisesRegex(policy.Refused, "generation"):
                self.plan(docs)

    def test_rejects_incomplete_or_invalid_split(self):
        for value in (99, -1, 101, True, "100"):
            docs = evidence()
            docs[0]["status"]["traffic"][0]["percent"] = value
            with self.assertRaises(policy.Refused):
                self.plan(docs)

    def test_rejects_unknown_probe_headers_or_sidecars(self):
        for field in ("headers", "sidecar"):
            docs = evidence()
            containers = docs[2]["spec"]["containers"]
            if field == "headers":
                containers[0]["startupProbe"]["httpGet"]["httpHeaders"] = [{"name": "Host", "value": "foreign"}]
            else:
                containers.append({})
            with self.assertRaises(policy.Refused):
                self.plan(docs)

    def test_cli_emits_plan_only_for_complete_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ("before.json", "current.json", "revision.json")]
            docs = evidence()
            for path, document in zip(paths, docs):
                path.write_text(json.dumps(document))
            command = [sys.executable, str(MODULE), "--before", str(paths[0]),
                       "--current", str(paths[1]), "--revision", str(paths[2]),
                       "--expected-revision", "service-new", "--expected-image", IMAGE,
                       "--health-path", "/healthz"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["promote_argument"], "service-new=100")
            docs[2]["status"]["conditions"] = []
            paths[2].write_text(json.dumps(docs[2]))
            denied = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertEqual(denied.returncode, 1)
            self.assertEqual(denied.stdout, "")


if __name__ == "__main__":
    unittest.main()
