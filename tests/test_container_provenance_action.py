"""Execute the repository-owned action with real shell and isolated runner files."""
import datetime
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ACTION = Path(__file__).resolve().parents[1] / "actions/container-provenance"
INDEX = "sha256:" + "1" * 64
PLATFORM = "sha256:" + "2" * 64
IMAGE = "registry.example.invalid/fixtures/provenance-image"
COMMIT = "3" * 40
REPOSITORY = "fixture/provenance-source"


class ContainerProvenanceActionTests(unittest.TestCase):
    def generate(self, index=INDEX, platform=PLATFORM, omit_env=(),
                 overrides=None, failure=None, stale_checkout=False):
        source = (ACTION / "action.yml").read_text()
        inputs = {
            "candidate-digest": index, "platform-digest": platform,
            "registry": "registry.example.invalid/fixtures",
            "image-name": "provenance-image", "context": "./services",
            "dockerfile": "./services/Dockerfile",
        }
        self.assertEqual(source.count("      run: "), 1)
        script = source.split("      run: ", 1)[1].strip()
        self.assertNotIn("${{", script, "Never interpolate metadata into the shell program")
        bindings = {}
        for name, value in re.findall(r"^        ([A-Z_]+): (.+)$", source, re.MULTILINE):
            if value == "${{ github.action_path }}":
                bindings[name] = str(ACTION)
            else:
                match = re.fullmatch(r"\$\{\{ inputs\.([a-z-]+) \}\}", value)
                self.assertIsNotNone(match, "Review new action environment bindings")
                bindings[name] = inputs[match[1]]
        with tempfile.TemporaryDirectory(prefix="provenance-action-contract-") as directory:
            root = Path(directory)
            tools = root / "bin"
            tools.mkdir()
            for tool in ("gcloud", "docker", "curl", "wget", "gh"):
                stub = tools / tool
                stub.write_text("#!/bin/sh\nexit 73\n")
                stub.chmod(0o755)
            python = tools / "python3"
            python.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -B \"$@\"\n")
            python.chmod(0o755)
            if failure == "python":
                python.write_text("#!/bin/sh\nexit 73\n")
            temporary = root / "runner-temp"
            temporary.mkdir()
            output = root / "github-output"
            if failure == "output":
                output.mkdir()
            if failure == "runner-temp":
                temporary.rmdir()
            if stale_checkout:
                for name in ("provenance.json", "platform-provenance.json"):
                    (root / name).write_text('{"stale":true}\n')
            env = {
                "PATH": str(tools) + os.pathsep + os.environ["PATH"], "HOME": directory,
                "GITHUB_RUN_ID": "2468", "GITHUB_RUN_ATTEMPT": "2",
                "GITHUB_OUTPUT": str(output), "RUNNER_TEMP": str(temporary),
                "GITHUB_REPOSITORY": REPOSITORY, "GITHUB_SHA": COMMIT,
                "GITHUB_REF": "refs/heads/main", "GITHUB_WORKFLOW": "Offline provenance contract",
                "GITHUB_ACTOR": "fixture-actor", **bindings, **(overrides or {}),
            }
            for variable in omit_env:
                env.pop(variable, None)
            result = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=10)
            self.assertFalse((root / "pwned").exists(), "Metadata must not become shell code")
            text = output.read_text() if output.is_file() else ""
            docs = {}
            for name in ("provenance", "platform_provenance"):
                match = re.search(r"^" + name + r"_path=(.+)$", text, re.MULTILINE)
                if match:
                    path = Path(match[1])
                    self.assertEqual(path.parent.parent, temporary)
                    self.assertEqual(path.name, name.replace("_", "-") + ".json")
                    docs[path.name] = json.loads(path.read_text())
            return result.returncode, docs, text, result.stderr

    def assert_canonical_subjects(self, statement):
        self.assertTrue(statement["subject"])
        for subject in statement["subject"]:
            self.assertEqual(subject["name"], IMAGE)
            self.assertEqual(set(subject["digest"]), {"sha256"})
            self.assertRegex(subject["digest"]["sha256"], r"\A[0-9a-f]{64}\Z")

    def assert_bound_predicate(self, statement):
        self.assertEqual(statement["_type"], "https://in-toto.io/Statement/v0.1")
        self.assertEqual(statement["predicateType"], "https://slsa.dev/provenance/v0.2")
        predicate = statement["predicate"]
        self.assertEqual(predicate["buildType"], "https://github.com/slsa-framework/slsa-github-generator/container@v1")
        self.assertEqual(predicate["builder"]["id"], "https://github.com/fixture/provenance-source/actions/runs/2468")
        self.assertEqual(predicate["invocation"]["configSource"], {
            "uri": "git+https://github.com/fixture/provenance-source@refs/heads/main",
            "digest": {"sha1": COMMIT}, "entryPoint": "Offline provenance contract",
        })
        self.assertEqual(predicate["invocation"]["parameters"], {})
        self.assertEqual(predicate["invocation"]["environment"], {
            "github_actor": "fixture-actor", "github_sha": COMMIT,
            "github_repository": REPOSITORY, "github_ref": "refs/heads/main", "github_run_id": "2468",
        })
        self.assertEqual(predicate["materials"], [{
            "uri": "git+https://github.com/fixture/provenance-source", "digest": {"sha1": COMMIT},
        }])
        self.assertEqual(predicate["buildConfig"], {"dockerfile": "./services/Dockerfile", "context": "./services"})
        self.assertEqual(predicate["metadata"]["buildInvocationId"], "2468-2")
        self.assertEqual(predicate["metadata"]["completeness"], {"parameters": True, "environment": True, "materials": True})
        self.assertIs(predicate["metadata"]["reproducible"], False)
        self.assertIsNotNone(datetime.datetime.fromisoformat(predicate["metadata"]["buildStartedOn"].replace("Z", "+00:00")).tzinfo)

    def assert_published_statement_matches(self, text, statement):
        lines = text.splitlines()
        header = next(line for line in lines if line.startswith("provenance<<"))
        delimiter = header.split("<<", 1)[1]
        start = lines.index(header) + 1
        end = lines.index(delimiter, start)
        self.assertEqual(json.loads("\n".join(lines[start:end])), statement)

    def test_distinct_index_and_platform_are_bare_hashes_with_bound_claims(self):
        code, docs, output, stderr = self.generate()
        self.assertEqual(code, 0, stderr)
        full, projected = docs["provenance.json"], docs["platform-provenance.json"]
        self.assert_canonical_subjects(full)
        self.assert_canonical_subjects(projected)
        self.assertEqual([item["digest"]["sha256"] for item in full["subject"]], [INDEX[7:], PLATFORM[7:]])
        self.assertEqual(projected["subject"], [{"name": IMAGE, "digest": {"sha256": PLATFORM[7:]}}])
        self.assert_bound_predicate(full)
        self.assertEqual(projected["predicate"], full["predicate"])
        self.assert_published_statement_matches(output, full)
        published = next(line for line in output.splitlines() if line.startswith("platform_provenance="))
        self.assertEqual(json.loads(published.split("=", 1)[1]), projected)

    def test_equal_index_and_platform_cannot_select_a_prefixed_subject(self):
        code, docs, output, stderr = self.generate(platform=INDEX)
        self.assertEqual(code, 0, stderr)
        for document in docs.values():
            self.assert_canonical_subjects(document)
            self.assert_bound_predicate(document)
        self.assertEqual({item["digest"]["sha256"] for item in docs["provenance.json"]["subject"]}, {INDEX[7:]})
        self.assertEqual(docs["platform-provenance.json"]["subject"], [{"name": IMAGE, "digest": {"sha256": INDEX[7:]}}])
        self.assert_published_statement_matches(output, docs["provenance.json"])
        published = next(line for line in output.splitlines() if line.startswith("platform_provenance="))
        self.assertEqual(json.loads(published.split("=", 1)[1]), docs["platform-provenance.json"])

    def test_no_platform_emits_only_the_canonical_index_statement(self):
        code, docs, output, stderr = self.generate(platform="")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(set(docs), {"provenance.json"})
        self.assert_canonical_subjects(docs["provenance.json"])
        self.assert_bound_predicate(docs["provenance.json"])
        self.assertEqual(docs["provenance.json"]["subject"], [{"name": IMAGE, "digest": {"sha256": INDEX[7:]}}])
        self.assertNotIn("platform_provenance=", output)
        self.assert_published_statement_matches(output, docs["provenance.json"])

    def test_malformed_index_never_publishes_statements(self):
        for invalid in ("", "1" * 64, "sha256:", "sha256:" + "1" * 63,
                        "sha256:" + "1" * 65, "sha256:" + "A" * 64,
                        "sha256:" + "z" * 64, INDEX + " ", INDEX + "\n", "sha512:" + "1" * 64):
            with self.subTest(value=repr(invalid)):
                code, docs, output, _ = self.generate(index=invalid)
                self.assertNotEqual(code, 0)
                self.assertEqual(docs, {})
                self.assertEqual(output, "")

    def test_malformed_nonempty_platform_never_publishes_statements(self):
        for invalid in ("2" * 64, "sha256:", "sha256:" + "2" * 63,
                        "sha256:" + "2" * 65, "sha256:" + "B" * 64,
                        "sha256:" + "z" * 64, PLATFORM + " ", PLATFORM + "\n", "sha512:" + "2" * 64):
            with self.subTest(value=repr(invalid)):
                code, docs, output, _ = self.generate(platform=invalid)
                self.assertNotEqual(code, 0)
                self.assertEqual(docs, {})
                self.assertEqual(output, "")

    def test_missing_candidate_fails_before_outputs(self):
        code, docs, output, _ = self.generate(omit_env=("CANDIDATE_DIGEST",))
        self.assertNotEqual(code, 0)
        self.assertEqual((docs, output), ({}, ""))

    def test_missing_optional_platform_emits_only_the_index(self):
        code, docs, output, stderr = self.generate(omit_env=("PLATFORM_DIGEST",))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(set(docs), {"provenance.json"})
        self.assertEqual(docs["provenance.json"]["subject"][0]["digest"]["sha256"], INDEX[7:])
        self.assertNotIn("platform_provenance", output)

    def test_invalid_or_missing_runner_identity_never_publishes(self):
        for variable, value in [("GITHUB_SHA", "bad"), ("GITHUB_RUN_ID", "0"),
                                ("GITHUB_RUN_ATTEMPT", "x"), ("GITHUB_REPOSITORY", "../bad"),
                                ("GITHUB_REPOSITORY", "owner/.."), ("GITHUB_REPOSITORY", "owner/."),
                                ("GITHUB_REPOSITORY", "-owner/repo"), ("GITHUB_REPOSITORY", "owner-/repo")]:
            with self.subTest(variable=variable, value=value):
                code, docs, output, _ = self.generate(overrides={variable: value})
                self.assertNotEqual(code, 0)
                self.assertEqual((docs, output), ({}, ""))
        for variable in ("GITHUB_REF", "GITHUB_WORKFLOW", "GITHUB_ACTOR",
                         "GITHUB_OUTPUT", "RUNNER_TEMP", "IMAGE_REGISTRY", "IMAGE_NAME"):
            with self.subTest(variable=variable):
                code, docs, output, _ = self.generate(omit_env=(variable,))
                self.assertNotEqual(code, 0)
                self.assertEqual((docs, output), ({}, ""))

    def test_action_tool_and_output_failures_are_not_success(self):
        for failure in ("python", "output", "runner-temp"):
            with self.subTest(failure=failure):
                code, docs, output, _ = self.generate(failure=failure)
                self.assertNotEqual(code, 0)
                self.assertEqual((docs, output), ({}, ""))

    def test_untrusted_metadata_remains_json_data_not_shell_or_output_commands(self):
        value = 'quote" slash\\ newline\n$(touch pwned)\nPROVENANCE_EOF_fake'
        code, docs, output, stderr = self.generate(overrides={
            "GITHUB_WORKFLOW": value, "GITHUB_REF": "refs/heads/" + value,
            "GITHUB_ACTOR": value, "BUILD_CONTEXT": value, "BUILD_DOCKERFILE": value,
        })
        self.assertEqual(code, 0, stderr)
        for document in docs.values():
            predicate = document["predicate"]
            self.assertEqual(predicate["invocation"]["configSource"]["entryPoint"], value)
            self.assertEqual(predicate["invocation"]["environment"]["github_actor"], value)
            self.assertEqual(predicate["buildConfig"], {"context": value, "dockerfile": value})
        self.assert_published_statement_matches(output, docs["provenance.json"])

    def test_stale_checkout_statement_cannot_be_uploaded_as_a_generated_platform(self):
        code, docs, output, stderr = self.generate(platform="", stale_checkout=True)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(set(docs), {"provenance.json"})
        self.assertNotIn("platform_provenance", output)
        self.assertEqual(docs["provenance.json"]["subject"][0]["digest"]["sha256"], INDEX[7:])


if __name__ == "__main__":
    unittest.main()
