"""Offline contract for the CodeQL scanner's installation-only canary mode."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit/codeql-conflict-scan.mjs"


class InstallationScopeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.calls = self.root / "calls.jsonl"
        self.proof = self.root / "environment-proof.json"
        self.output = self.root / "isolated-output"
        self.scope = self.root / "approved.json"
        self.scope.write_text(json.dumps({
            "version": 1,
            "organization": "merglbot-core",
            "repositories": [{"id": 17, "name": "example"}],
        }))
        gh = self.bin / "gh"
        gh.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ['GH_CALLS']).open('a') as log:
    log.write(json.dumps(args) + '\\n')
Path(os.environ['GH_PROOF']).write_text(json.dumps({
    'fallback_empty': os.environ.get('GITHUB_TOKEN') == '',
    'prompt_disabled': os.environ.get('GH_PROMPT_DISABLED') == '1',
    'isolated_config': Path(os.environ.get('GH_CONFIG_DIR', '')).name.startswith('merglbot-codeql-gh-'),
}))
path = args[1]
if path.startswith('installation/repositories?'):
    repo_id = 18 if os.environ.get('GH_FIXTURE_MODE') == 'mismatch' else 17
    repos = [{'id': repo_id, 'name': 'example', 'full_name': 'merglbot-core/example',
              'archived': False, 'disabled': False}]
    if os.environ.get('GH_FIXTURE_MODE') == 'partial':
        repos.append({'id': 18, 'name': 'second', 'full_name': 'merglbot-core/second',
                      'archived': False, 'disabled': False})
    print(json.dumps([{'total_count': len(repos), 'repositories': repos}]))
elif path == 'repos/merglbot-core/example/code-scanning/default-setup':
    print(json.dumps({'state': 'not-configured', 'languages': []}))
elif path == 'repos/merglbot-core/example/contents/.github/workflows':
    print('[]')
elif path == 'repos/merglbot-core/second/code-scanning/default-setup':
    print('HTTP 403 not authorized to read code scanning', file=sys.stderr)
    sys.exit(1)
elif path == 'user/orgs':
    print(json.dumps([[{'login': 'merglbot-core'}]]))
elif path.startswith('orgs/merglbot-core/repos?'):
    print(json.dumps([[{'id': 17, 'name': 'example',
                        'full_name': 'merglbot-core/example',
                        'archived': False, 'disabled': False}]]))
else:
    sys.exit(21)
""")
        gh.chmod(0o755)

    def run_scanner(self, *, token=True, fixture_mode=None, org="merglbot-core"):
        scope = json.loads(self.scope.read_text())
        scope["organization"] = org
        if fixture_mode == "partial":
            scope["repositories"].append({"id": 18, "name": "second"})
        self.scope.write_text(json.dumps(scope))
        env = os.environ.copy()
        env.update({"HOME": str(self.root), "PATH": f"{self.bin}:{env['PATH']}",
                    "GH_CALLS": str(self.calls), "GH_PROOF": str(self.proof),
                    "GITHUB_TOKEN": "fixture-only"})
        env.pop("GH_HOST", None)
        if token:
            env["GH_TOKEN"] = "fixture-only"
        else:
            env.pop("GH_TOKEN", None)
        if fixture_mode:
            env["GH_FIXTURE_MODE"] = fixture_mode
        return subprocess.run(
            ["node", str(SCRIPT), "--installation-scope", str(self.scope),
             "--output-dir", str(self.output), "--json", "--quiet"],
            env=env, capture_output=True, text=True, timeout=15, check=False)

    def calls_made(self):
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def test_exact_installation_scope_scans_without_user_or_org_discovery(self):
        result = self.run_scanner()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual((report["status"], report["repos_swept"], report["repos_active"]),
                         ("OK", 1, 1))
        paths = [call[1] for call in self.calls_made()]
        self.assertEqual(paths, [
            "installation/repositories?per_page=100",
            "repos/merglbot-core/example/code-scanning/default-setup",
            "repos/merglbot-core/example/contents/.github/workflows",
        ])
        self.assertTrue((self.output / "latest.json").exists())
        self.assertFalse((self.root / ".merglbot/codeql-conflict/latest.json").exists())
        self.assertEqual(json.loads(self.proof.read_text()), {
            "fallback_empty": True, "prompt_disabled": True, "isolated_config": True,
        })

    def test_existing_scheduled_mode_still_uses_its_own_output_and_discovery(self):
        env = os.environ.copy()
        env.update({"HOME": str(self.root), "PATH": f"{self.bin}:{env['PATH']}",
                    "GH_CALLS": str(self.calls), "GH_PROOF": str(self.proof)})
        result = subprocess.run(["node", str(SCRIPT), "--json", "--quiet"],
                                env=env, capture_output=True, text=True,
                                timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[1] for call in self.calls_made()][:2],
                         ["user/orgs", "orgs/merglbot-core/repos?per_page=100&type=all"])
        self.assertTrue((self.root / ".merglbot/codeql-conflict/latest.json").exists())

    def test_mismatched_installation_stops_before_repository_reads(self):
        result = self.run_scanner(fixture_mode="mismatch")
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["errors"], ["installation_scope_mismatch"])
        self.assertEqual(len(self.calls_made()), 1)

    def test_missing_explicit_token_never_uses_personal_keyring(self):
        result = self.run_scanner(token=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls_made(), [])
        self.assertEqual(json.loads(result.stdout)["errors"],
                         ["installation_scope_unavailable"])

    def test_partial_permission_coverage_is_degraded_and_nonzero(self):
        result = self.run_scanner(fixture_mode="partial")
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual((report["status"], report["repos_swept"], report["repos_active"]),
                         ("DEGRADED", 1, 2))
        self.assertEqual(report["unswept"], ["merglbot-core/second"])
        self.assertEqual(report["unreadable"], ["merglbot-core/second"])

    def test_excluded_org_fails_before_network(self):
        result = self.run_scanner(org="lrtch")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls_made(), [])


if __name__ == "__main__":
    unittest.main()
