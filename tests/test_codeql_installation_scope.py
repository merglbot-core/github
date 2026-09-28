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
mode = os.environ.get('GH_FIXTURE_MODE')
if path.startswith('installation/repositories?'):
    repo_id = 18 if mode == 'mismatch' else 17
    repos = [{'id': repo_id, 'name': 'example', 'full_name': 'merglbot-core/example',
              'archived': False, 'disabled': False}]
    if mode in ('partial', 'partial_conflict'):
        repos.append({'id': 18, 'name': 'second', 'full_name': 'merglbot-core/second',
                      'archived': False, 'disabled': False})
    print(json.dumps([{'total_count': len(repos), 'repositories': repos}]))
elif path == 'repos/merglbot-core/example/code-scanning/default-setup':
    if mode == 'ambiguous_404':
        print('HTTP 404 Resource not found', file=sys.stderr)
        sys.exit(1)
    state = 'unknown' if mode == 'unknown_state' else 'configured' if mode in ('reusable', 'unrelated', 'real_conflict',
                                     'partial_conflict', 'unreadable_candidate',
                                     'commented_action', 'dual_trigger', 'reusable_caller',
                                     'nested_reusable', 'directory_object', 'mixed_case_action',
                                     'local_composite', 'direct_and_local',
                                     'reusable_conflict_then_unreadable',
                                     'conflict_then_unreadable') else 'not-configured'
    print(json.dumps({'state': state, 'languages': ['javascript']}))
elif path == 'repos/merglbot-core/example/contents/.github/workflows':
    if mode == 'directory_object':
        print(json.dumps({'unexpected': True}))
        sys.exit(0)
    print(json.dumps([{'name': 'codeql.yml'}] +
                     ([{'name': 'later.yml'}] if mode == 'conflict_then_unreadable' else [])) if mode in
          ('reusable', 'unrelated', 'real_conflict', 'partial_conflict',
           'unreadable_candidate', 'commented_action', 'dual_trigger',
           'reusable_caller', 'nested_reusable', 'conflict_then_unreadable',
           'mixed_case_action', 'local_composite', 'direct_and_local',
           'reusable_conflict_then_unreadable') else '[]')
elif path == 'repos/merglbot-core/example/contents/.github/workflows/codeql.yml':
    if mode == 'unreadable_candidate':
        print('HTTP 403 not authorized', file=sys.stderr)
        sys.exit(1)
    print('on:')
    print('  workflow_call:' if mode == 'reusable' else '  push:')
    if mode == 'dual_trigger':
        print('  workflow_call:')
    print('jobs:')
    print('  scan:')
    if mode in ('reusable_caller', 'nested_reusable', 'reusable_conflict_then_unreadable'):
        print('    uses: merglbot-core/github/.github/workflows/reusable-codeql-analysis.yml@0123456789abcdef0123456789abcdef01234567')
        if mode == 'reusable_conflict_then_unreadable':
            print('  later:')
            print('    uses: merglbot-core/github/.github/workflows/missing.yml@0123456789abcdef0123456789abcdef01234567')
    else:
        print('    steps:')
        if mode == 'commented_action':
            print('      # - uses: github/codeql-action/analyze@v4')
            print('      - uses: actions/checkout@v4')
        elif mode == 'local_composite':
            print('      - uses: ./.github/actions/scan')
        elif mode == 'direct_and_local':
            print('      - uses: github/codeql-action/analyze@v4')
            print('      - uses: ./.github/actions/scan')
        elif mode == 'mixed_case_action':
            print('      - uses: GitHub/codeql-action/analyze@v4')
        elif mode == 'unrelated':
            print('      - uses: actions/checkout@v4')
        else:
            print('      - uses: github/codeql-action/analyze@v4')
elif path == 'repos/merglbot-core/github/contents/.github/workflows/reusable-codeql-analysis.yml?ref=0123456789abcdef0123456789abcdef01234567':
    print('on: workflow_call')
    print('jobs:')
    print('  scan:')
    if mode == 'nested_reusable':
        print('    uses: ./.github/workflows/nested.yml')
        sys.exit(0)
    print('    steps:')
    print('      - uses: github/codeql-action/analyze@v4')
elif path == 'repos/merglbot-core/github/contents/.github/workflows/nested.yml?ref=0123456789abcdef0123456789abcdef01234567':
    print('on: workflow_call')
    print('jobs:')
    print('  scan:')
    print('    steps:')
    print('      - uses: github/codeql-action/analyze@v4')
elif path == 'repos/merglbot-core/github/contents/.github/workflows/missing.yml?ref=0123456789abcdef0123456789abcdef01234567':
    print('HTTP 403 not authorized', file=sys.stderr)
    sys.exit(1)
elif path == 'repos/merglbot-core/example/contents/.github/workflows/later.yml':
    print('HTTP 403 not authorized', file=sys.stderr)
    sys.exit(1)
elif path == 'repos/merglbot-core/second/code-scanning/default-setup':
    print('HTTP 403 not authorized to read code scanning', file=sys.stderr)
    sys.exit(1)
elif path == 'user/orgs':
    print(json.dumps([[{'login': 'merglbot-core'}]]))
elif path.startswith('orgs/merglbot-core/repos?'):
    repos = [{'id': 17, 'name': 'example',
              'full_name': 'merglbot-core/example',
              'archived': False, 'disabled': False}]
    if mode == 'legacy_partial':
        repos.append({'id': 18, 'name': 'second', 'full_name': 'merglbot-core/second',
                      'archived': False, 'disabled': False})
    print(json.dumps([repos]))
else:
    sys.exit(21)
""")
        gh.chmod(0o755)

    def run_scanner(self, *, token=True, fixture_mode=None, org="merglbot-core"):
        scope = json.loads(self.scope.read_text())
        scope["organization"] = org
        if fixture_mode in ("partial", "partial_conflict"):
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

    def test_existing_scheduled_mode_permission_gap_is_degraded(self):
        env = os.environ.copy()
        env.update({"HOME": str(self.root), "PATH": f"{self.bin}:{env['PATH']}",
                    "GH_CALLS": str(self.calls), "GH_PROOF": str(self.proof),
                    "GH_FIXTURE_MODE": "legacy_partial"})
        result = subprocess.run(["node", str(SCRIPT), "--json", "--quiet"],
                                env=env, capture_output=True, text=True,
                                timeout=15, check=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual((report["status"], report["repos_swept"], report["repos_active"]),
                         ("DEGRADED", 1, 2))
        self.assertEqual(report["unswept"], ["merglbot-core/second"])

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

    def test_ambiguous_codeql_404_cannot_be_clean(self):
        result = self.run_scanner(fixture_mode="ambiguous_404")
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "ERROR")
        self.assertEqual(report["unswept"], ["merglbot-core/example"])
        self.assertEqual(len(self.calls_made()), 2)

    def test_reusable_only_matching_filename_is_not_a_conflict(self):
        report = json.loads(self.run_scanner(fixture_mode="reusable").stdout)
        self.assertEqual((report["status"], report["conflicts"]), ("OK", []))
        self.assertIn('repos/merglbot-core/example/contents/.github/workflows/codeql.yml',
                      [call[1] for call in self.calls_made()])

    def test_unrelated_matching_filename_is_not_a_conflict(self):
        result = self.run_scanner(fixture_mode="unrelated")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["conflicts"], [])

    def test_executing_codeql_workflow_is_a_conflict(self):
        result = self.run_scanner(fixture_mode="real_conflict")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "CONFLICT")

    def test_commented_action_is_not_a_conflict(self):
        result = self.run_scanner(fixture_mode="commented_action")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["conflicts"], [])

    def test_workflow_call_plus_push_is_executable(self):
        result = self.run_scanner(fixture_mode="dual_trigger")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "CONFLICT")

    def test_reusable_caller_follows_actual_workflow(self):
        result = self.run_scanner(fixture_mode="reusable_caller")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "CONFLICT")
        self.assertIn('repos/merglbot-core/github/contents/.github/workflows/reusable-codeql-analysis.yml?ref=0123456789abcdef0123456789abcdef01234567',
                      [call[1] for call in self.calls_made()])

    def test_nested_reusable_keeps_pinned_ref(self):
        result = self.run_scanner(fixture_mode="nested_reusable")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('repos/merglbot-core/github/contents/.github/workflows/nested.yml?ref=0123456789abcdef0123456789abcdef01234567',
                      [call[1] for call in self.calls_made()])

    def test_reusable_conflict_survives_later_unreadable_call(self):
        result = self.run_scanner(fixture_mode="reusable_conflict_then_unreadable")
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "CONFLICT_PARTIAL")
        self.assertEqual(report["conflicts"][0]["advanced_workflows"], ["codeql.yml"])
        self.assertEqual(report["unswept"], ["merglbot-core/example"])

    def test_conflict_survives_later_unreadable_workflow(self):
        result = self.run_scanner(fixture_mode="conflict_then_unreadable")
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "CONFLICT_PARTIAL")
        self.assertEqual(report["unswept"], ["merglbot-core/example"])
        self.assertEqual(report["conflicts"][0]["advanced_workflows"], ["codeql.yml"])

    def test_unknown_default_setup_state_is_unswept(self):
        result = self.run_scanner(fixture_mode="unknown_state")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["unswept"], ["merglbot-core/example"])

    def test_invalid_workflow_directory_is_unswept(self):
        result = self.run_scanner(fixture_mode="directory_object")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["unswept"], ["merglbot-core/example"])

    def test_mixed_case_codeql_action_is_conflict(self):
        result = self.run_scanner(fixture_mode="mixed_case_action")
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_local_composite_is_unverified(self):
        result = self.run_scanner(fixture_mode="local_composite")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["unswept"], ["merglbot-core/example"])

    def test_direct_conflict_survives_unverified_composite(self):
        result = self.run_scanner(fixture_mode="direct_and_local")
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "CONFLICT_PARTIAL")
        self.assertEqual(report["conflicts"][0]["repo"], "merglbot-core/example")

    def test_conflict_with_unswept_repo_exits_as_incomplete(self):
        result = self.run_scanner(fixture_mode="partial_conflict")
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "CONFLICT_PARTIAL")
        self.assertEqual(report["unswept"], ["merglbot-core/second"])
        self.assertEqual([c["repo"] for c in report["conflicts"]],
                         ["merglbot-core/example"])

    def test_unreadable_matching_workflow_is_unswept(self):
        result = self.run_scanner(fixture_mode="unreadable_candidate")
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["unswept"], ["merglbot-core/example"])
        self.assertEqual(report["conflicts"], [])

    def test_excluded_org_fails_before_network(self):
        result = self.run_scanner(org="lrtch")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls_made(), [])


if __name__ == "__main__":
    unittest.main()
