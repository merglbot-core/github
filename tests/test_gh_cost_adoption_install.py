"""Tests for tools/gh-cost-autopilot-adoption/install.py in a temporary HOME."""
import hashlib
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/gh-cost-autopilot-adoption"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(data):
    return hashlib.sha256(data).hexdigest()


pa = load("adoption_patch_for_install", TOOL / "patch_autopilot.py")
# The anchors are code fragments; wrapped in a raw string the fixture compiles before and after
# patching (patch_autopilot.py itself uses ''' delimiters, so no anchor contains them).
FIXTURE = ("SOURCE = r'''\n" + "".join(f"# segment {n}\n{before}\n" for n, (before, _)
                                        in enumerate(pa.REPLACEMENTS_888)) + "'''\n")
FIXTURE_910 = ("SOURCE = r'''\n" + "".join(f"# segment {n}\n{before}\n" for n, (before, _)
                                            in enumerate(pa.REPLACEMENTS_910)) + "'''\n")
STATE_888 = {"dod": {"892|merglbot-core/merglbot-admin": {"owner_exception": {"text": "x"}, "met_at": "t"},
                     "895|merglbot-denatura/acquisition-analysis": {"sub": 895},
                     "895|merglbot-proteinaco/acquisition-analysis": {"sub": 895},
                     "889|o/r": {"sub": 889}},
             "subs": {"895": {}}, "prs": {}}
STATE_910 = {"dod": dict({"912|merglbot-core/forecast-engine": {"ignored_run_ids": {"1": "x"}}},
                         **{f"917|o/r{n}": {"kind": "arm64_pilot"} for n in range(5)}),
             "subs": {"917": {"technical_hold": True}, "921": {"technical_hold": False}}}


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.ins = load("adoption_install", TOOL / "install.py")
        self.ins.HOLD = self.tmp / "OWNER_HOLD"
        self.ins.LAUNCHD_PYTHON = sys.executable  # CI has no macOS CommandLineTools python
        self.base = self.tmp / "gh-cost"
        (self.base / "autopilot").mkdir(parents=True)
        (self.base / "autopilot.py").write_text(FIXTURE)
        self.write_state(STATE_888)
        self.ins.TARGETS = {"888": {"base": self.base, "patch": "patch_888", "adopted": sha(FIXTURE.encode())}}

    def write_state(self, state):
        (self.base / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")

    def digests(self):
        return sha((self.base / "autopilot.py").read_bytes()), sha((self.base / "state.json").read_bytes())

    def install(self, dry_run=False):
        code, state = self.digests()
        return self.ins.install_code("888", code, state, dry_run)


class Code(Home):
    def test_dry_run_writes_nothing(self):
        result = self.install(dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual((self.base / "autopilot.py").read_text(), FIXTURE)
        self.assertFalse((self.base / "cost_adoption.py").exists())
        self.assertTrue(all(v == "no-op" or v.startswith(("refused", "aims at"))
                            for v in result["older_patchers"].values()))
        # tools/gh-cost-910-closeout installs only into ~/.merglbot/gh-cost-910
        self.assertEqual(result["older_patchers"]["gh-cost-910-closeout"], "aims at gh-cost-910")

    def test_install_backs_up_writes_and_a_rerun_is_a_no_op(self):
        result = self.install()
        self.assertEqual(sha((self.base / "autopilot.py").read_bytes()), result["code_after"])
        self.assertEqual((self.base / "cost_adoption.py").read_bytes(), (TOOL / "cost_adoption.py").read_bytes())
        backup = pathlib.Path(result["backup"])
        self.assertEqual((backup / "autopilot.py").read_text(), FIXTURE)
        self.assertEqual(json.loads((backup / "manifest.json").read_text())["code_before"], sha(FIXTURE.encode()))
        self.assertFalse((self.base / "autopilot/lock").exists())
        self.assertTrue(self.install()["noop"])

    def test_refusals(self):
        code, state = self.digests()
        with self.assertRaisesRegex(RuntimeError, "fresh digests"):
            self.ins.install_code("888", "0" * 64, state)
        self.ins.TARGETS["888"]["adopted"] = "f" * 64
        with self.assertRaisesRegex(RuntimeError, "unreviewed pre-image"):
            self.ins.install_code("888", code, state)
        self.ins.TARGETS["888"]["adopted"] = sha(FIXTURE.encode())
        self.ins.HOLD.write_text("hold")
        with self.assertRaisesRegex(RuntimeError, "OWNER_HOLD"):
            self.ins.install_code("888", code, state)
        self.ins.HOLD.unlink()
        (self.base / "autopilot/lock").mkdir()
        with self.assertRaises(FileExistsError):
            self.ins.install_code("888", code, state)
        (self.base / "autopilot/lock").rmdir()
        self.write_state({"dod": {}, "subs": {}})
        code, state = self.digests()
        with self.assertRaisesRegex(RuntimeError, "adopted edits"):
            self.ins.install_code("888", code, state)

    def test_rollback_only_from_the_installed_image(self):
        result = self.install()
        (self.base / "autopilot.py").write_text(FIXTURE + "# newer\n")
        with self.assertRaisesRegex(RuntimeError, "newer code"):
            self.ins.rollback("888", result["backup"])
        (self.base / "autopilot.py").write_bytes(pa.patch_888(FIXTURE).encode())
        self.assertEqual(self.ins.rollback("888", result["backup"])["restored"], sha(FIXTURE.encode()))
        self.assertFalse((self.base / "cost_adoption.py").exists())


class SourceMatchesMain(unittest.TestCase):
    def setUp(self):
        self.ins = load("adoption_install_source", TOOL / "install.py")

    def fetch(self, changed=None, sha="a" * 40, remaining=4000):
        import base64

        def read(path):
            if path.endswith("/branches/main"):
                return {"commit": {"sha": sha}}, remaining
            name = path.split("tools/gh-cost-autopilot-adoption/")[1].split("?")[0]
            data = (TOOL / name).read_bytes() if name != changed else b"changed"
            return {"encoding": "base64", "content": base64.b64encode(data).decode()}, remaining
        return read

    def test_every_source_file_must_equal_main(self):
        self.assertEqual(self.ins.source_matches_main(self.fetch()), "a" * 40)
        for name in self.ins.SOURCE_FILES:
            with self.assertRaisesRegex(RuntimeError, "differs from main"):
                self.ins.source_matches_main(self.fetch(changed=name))
        with self.assertRaisesRegex(RuntimeError, "unreadable"):
            self.ins.source_matches_main(self.fetch(sha="short"))
        with self.assertRaisesRegex(RuntimeError, "reserve"):
            self.ins.source_matches_main(self.fetch(remaining=10))


class Provenance(Home):
    """V6 #974: "installed" is the receipt-verified image, rollback validates before writing."""

    def test_patched_blocks_with_other_changes_are_not_installed(self):
        self.install()
        tampered = (self.base / "autopilot.py").read_text() + "# an edit outside every anchor\n"
        (self.base / "autopilot.py").write_text(tampered)
        code, state = self.digests()
        with self.assertRaisesRegex(RuntimeError, "not the reviewed installation"):
            self.ins.install_code("888", code, state)
        with self.assertRaisesRegex(RuntimeError, "install the adopted code first"):
            self.ins.code_installed("888")

    def test_a_changed_helper_is_not_installed_and_blocks_rollback(self):
        result = self.install()
        (self.base / "cost_adoption.py").write_text("# newer helper\n")
        with self.assertRaisesRegex(RuntimeError, "install the adopted code first"):
            self.ins.code_installed("888")
        with self.assertRaisesRegex(RuntimeError, "newer code or helper"):
            self.ins.rollback("888", result["backup"])
        self.assertEqual((self.base / "cost_adoption.py").read_text(), "# newer helper\n")

    def test_a_corrupt_or_foreign_backup_is_refused_before_any_write(self):
        result = self.install()
        backup = pathlib.Path(result["backup"])
        live = (self.base / "autopilot.py").read_bytes()
        (backup / "autopilot.py").write_text("corrupt\n")
        with self.assertRaisesRegex(RuntimeError, "does not match its manifest"):
            self.ins.rollback("888", result["backup"])
        self.assertEqual((self.base / "autopilot.py").read_bytes(), live)
        (backup / "autopilot.py").write_text(FIXTURE)
        (backup / "cost_adoption.py").write_text("unexpected\n")
        with self.assertRaisesRegex(RuntimeError, "does not match its manifest"):
            self.ins.rollback("888", result["backup"])
        (backup / "cost_adoption.py").unlink()
        manifest = (backup / "manifest.json").read_text()
        (backup / "manifest.json").write_text(manifest.replace('"target": "888"', '"target": "910"'))
        with self.assertRaisesRegex(RuntimeError, "does not match its manifest"):
            self.ins.rollback("888", result["backup"])
        (backup / "manifest.json").write_text(manifest)
        self.assertEqual(self.ins.rollback("888", result["backup"])["restored"], sha(FIXTURE.encode()))
        self.assertFalse((self.base / self.ins.RECEIPT).exists())

    def test_rollback_needs_the_protected_main_source(self):
        import sys
        result = self.install()
        calls = []
        self.ins.source_matches_main = lambda: calls.append("main") or (_ for _ in ()).throw(
            RuntimeError("differs from main"))
        saved, sys.argv = sys.argv, ["install.py", "rollback", "--target", "888", "--backup", result["backup"]]
        try:
            with self.assertRaisesRegex(RuntimeError, "differs from main"):
                self.ins.main()
        finally:
            sys.argv = saved
        self.assertEqual(calls, ["main"])
        self.assertEqual((self.base / "autopilot.py").read_bytes(), pa.patch_888(FIXTURE).encode())


class TwoPhase(unittest.TestCase):
    def test_no_target_is_written_when_another_fails_validation(self):
        import sys
        ins = load("adoption_install_two_phase", TOOL / "install.py")
        calls = []

        def fake(target, code, state, dry_run=False, source=None):
            calls.append((target, dry_run))
            if target == "910" and dry_run:
                raise RuntimeError("910 refuses")
            return {"target": target}
        ins.install_code, ins.source_matches_main = fake, lambda: "a" * 40
        argv = ["install.py", "code"] + [f"--expected-{k}-{t}=x" for t in ("888", "910") for k in ("code", "state")]
        saved, sys.argv = sys.argv, argv
        try:
            with self.assertRaisesRegex(RuntimeError, "910 refuses"):
                ins.main()
        finally:
            sys.argv = saved
        self.assertEqual(calls, [("888", True), ("910", True)])


class OwnerException(Home):
    def setUp(self):
        super().setUp()
        self.install()  # state records come after the code

    def test_refused_before_the_code_is_installed(self):
        (self.base / "autopilot.py").write_text(FIXTURE)
        with self.assertRaisesRegex(RuntimeError, "install the adopted code first"):
            self.ins.record_owner_exception(self.digests()[1], fetch=self.fetch())

    def fetch(self, body=None, merged=False):
        body = body if body is not None else (TOOL / "decisions/895.cs.md").read_text()
        answers = {
            "repos/merglbot-core/github/issues/comments/5917584272": {
                "body": body, "created_at": "2026-09-30T18:52:19Z",
                "issue_url": "https://api.github.com/repos/merglbot-core/github/issues/895"},
            "repos/merglbot-denatura/acquisition-analysis/pulls/170": {"state": "closed", "merged": merged},
            "repos/merglbot-proteinaco/acquisition-analysis/pulls/184": {"state": "closed", "merged": False},
            "repos/merglbot-denatura/acquisition-analysis/branches/main/protection/required_status_checks":
                {"checks": [{"context": "gitleaks / Secret Scanning"}]},
            "repos/merglbot-proteinaco/acquisition-analysis/branches/main/protection/required_status_checks":
                {"checks": [{"context": "gitleaks"}, {"context": "dependency-review"}]},
        }
        return lambda path: (answers[path], 4000)

    def test_writes_exactly_the_two_rows(self):
        before = json.loads((self.base / "state.json").read_text())
        result = self.ins.record_owner_exception(self.digests()[1], fetch=self.fetch())
        after = json.loads((self.base / "state.json").read_text())
        rows = [k for k in after["dod"] if after["dod"][k] != before["dod"][k]]
        self.assertEqual(sorted(rows), sorted(self.ins.ROWS_895))
        record = after["dod"]["895|merglbot-proteinaco/acquisition-analysis"]["owner_exception"]
        self.assertEqual((record["pr"], record["required_contexts"], record["decided_at_prague"]),
                         ("merglbot-proteinaco/acquisition-analysis#184", ["gitleaks"], "30. 9. 2026"))
        self.assertTrue(pathlib.Path(result["backup"]).joinpath("state.json").exists())

    def test_wrong_record_or_merged_pr_is_refused(self):
        state = self.digests()[1]
        with self.assertRaisesRegex(RuntimeError, "reviewed #895 record"):
            self.ins.record_owner_exception(state, fetch=self.fetch(body="changed"))
        with self.assertRaisesRegex(RuntimeError, "PR merged/open"):
            self.ins.record_owner_exception(state, fetch=self.fetch(merged=True))


class ReleaseHold(Home):
    def setUp(self):
        super().setUp()
        self.base = self.tmp / "gh-cost-910"
        (self.base / "autopilot").mkdir(parents=True)
        (self.base / "autopilot.py").write_text(FIXTURE_910)
        self.write_state(STATE_910)
        self.ins.TARGETS["910"] = {"base": self.base, "patch": "patch_910", "adopted": sha(FIXTURE_910.encode())}
        code, state = self.digests()
        self.ins.install_code("910", code, state)

    def graphql(self, option):
        return lambda query: {"node": {"project": {"id": self.ins.PROJECT_66},
                                       "fieldValueByName": {"optionId": option}}}

    def test_needs_closed_and_done(self):
        state = self.digests()[1]
        with self.assertRaisesRegex(RuntimeError, "not closed as completed and Done"):
            self.ins.release_hold(state, fetch=lambda p: ({"state": "open"}, 4000),
                                  graphql=self.graphql(self.ins.DONE_66))
        with self.assertRaisesRegex(RuntimeError, "not closed as completed and Done"):
            self.ins.release_hold(state, fetch=lambda p: ({"state": "closed", "state_reason": "not_planned"}, 4000),
                                  graphql=self.graphql(self.ins.DONE_66))
        self.ins.release_hold(state, fetch=lambda p: ({"state": "closed", "state_reason": "completed"}, 4000),
                              graphql=self.graphql(self.ins.DONE_66))
        record = json.loads((self.base / "state.json").read_text())["subs"]["917"]
        self.assertIs(record["technical_hold"], False)
        self.assertTrue(record["board_done_at"])


if __name__ == "__main__":
    unittest.main()
