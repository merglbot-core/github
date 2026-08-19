"""Contract tests for the frontend build-artifact gate (merglbot-core/infra#1912).

The gate is S5 preventive components 3, 4 and 5a from the 2026-08-05 external security audit. These
tests DRIVE the real `gate.mjs` as a subprocess against temporary `dist/` trees, rather than
asserting on its source — a gate is only worth what its exit codes do, and every mutation below was
run to confirm it produces the exit code claimed.

The three exit codes are the contract:

    0  the artifact satisfies the baseline
    1  violation — the artifact is wrong
    2  cannot conclude — no dist, no index.html, below the file floor, malformed baseline

Exit 2 is the one that is easy to get wrong and the reason several tests exist purely for it: a gate
that returns 0 over an empty directory returns 0 over every build that produced nothing, which is
precisely the failure this component was prescribed to prevent.

unittest.TestCase deliberately: the runner is `python3 -m unittest discover`, which silently
collects zero from pytest-style module-level functions (merglbot-core/infra#1712).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = REPO_ROOT / "scripts" / "frontend-artifact-gate" / "gate.mjs"

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_CANNOT_CONCLUDE = 2

BASELINE = {
    "required_paths": [
        {"path": "robots.txt", "reason": "SEO/hygiene", "permanent": True},
        {
            "path": ".well-known/security.txt",
            "reason": "WEB-HYGIENE, external audit 2026-08",
            "permanent": True,
        },
    ],
    "banned_globs": [
        {"glob": "**/*.map", "reason": "source maps ship original sources", "permanent": True},
        {"glob": "**/.env*", "reason": "never ship env files", "permanent": True},
    ],
    "allowed_extensions": [".js", ".css", ".html", ".txt", ".png", ".svg"],
    "min_files": 3,
}


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


@unittest.skipUnless(shutil.which("node"), "node is required to drive the gate")
class FrontendArtifactGateTests(unittest.TestCase):
    def run_gate(self, files: dict[str, bytes], baseline: dict | None = None, extra=None):
        """Build a temp dist/ from `files`, run the real gate, return (returncode, output)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            for name, content in files.items():
                target = dist / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            baseline_path = root / "baseline.json"
            baseline_path.write_text(
                json.dumps(BASELINE if baseline is None else baseline), encoding="utf-8"
            )
            cmd = ["node", str(GATE), "--dist", str(dist), "--baseline", str(baseline_path)]
            if extra is not None:
                extra_dir = root / "packages"
                for name, content in extra.items():
                    target = extra_dir / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                cmd += ["--extra-assets", str(extra_dir)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            return proc.returncode, proc.stdout + proc.stderr

    def clean_dist(self) -> dict[str, bytes]:
        return {
            "index.html": b"<!doctype html>",
            "robots.txt": b"User-agent: *\n",
            ".well-known/security.txt": b"Contact: mailto:security@merglbot.ai\n",
            "assets/index-abc123.js": b"console.log(1)",
        }

    # --- the contract holds ---------------------------------------------------------------

    def test_a_clean_build_passes(self) -> None:
        rc, out = self.run_gate(self.clean_dist())
        self.assertEqual(EXIT_OK, rc, out)

    # --- violations (exit 1) --------------------------------------------------------------

    def test_a_nested_source_map_is_a_violation(self) -> None:
        """Nested, not top-level — a `endsWith('.map')` check would pass a `.map/` directory and a
        top-level-only glob would miss `assets/deep/`."""
        files = self.clean_dist()
        files["assets/deep/index-abc123.js.map"] = b'{"version":3}'
        rc, out = self.run_gate(files)
        self.assertEqual(EXIT_VIOLATION, rc, out)
        self.assertIn("index-abc123.js.map", out)

    def test_an_inline_source_map_is_a_violation_even_with_no_map_file(self) -> None:
        """The half the `**/*.map` glob cannot see.

        `build.sourcemap: 'inline'` writes no `.map` at all — it appends the whole map, base64
        encoded, into the bundle. dist/ then looks exactly like a clean build while the original
        sources ship inside a file that is already public.

        The assertion that there is no `.map` in the fixture is load-bearing: without it this test
        could pass on the filename rule and prove nothing about the byte scan.
        """
        files = self.clean_dist()
        files["assets/index-abc123.js"] = (
            b"console.log(1)\n"
            b"//# sourceMappingURL=data:application/json;base64,eyJ2ZXJzaW9uIjozfQ==\n"
        )
        self.assertFalse([f for f in files if f.endswith(".map")])
        rc, out = self.run_gate(files)
        self.assertEqual(EXIT_VIOLATION, rc, out)
        self.assertIn("sourceMappingURL", out)
        self.assertIn("index-abc123.js", out)

    def test_an_inline_source_map_inside_index_html_is_a_violation(self) -> None:
        """`.html` is a served text type, and an inline-script build shape puts the directive
        there — where a scan restricted to script extensions would never look."""
        files = self.clean_dist()
        files["index.html"] = (
            b"<!doctype html><script>console.log(1)\n"
            b"//# sourceMappingURL=data:application/json;base64,eyJ2ZXJzaW9uIjozfQ==\n"
            b"</script>"
        )
        rc, out = self.run_gate(files)
        self.assertEqual(EXIT_VIOLATION, rc, out)
        self.assertIn("index.html", out)

    def test_a_hidden_source_map_is_still_caught_by_the_filename_rule(self) -> None:
        """The mirror case, pinned so the two halves are not confused for one another:
        `sourcemap: 'hidden'` emits the FILE and omits the directive."""
        files = self.clean_dist()
        files["assets/index-abc123.js.map"] = b'{"version":3}'
        self.assertNotIn(b"sourceMappingURL", files["assets/index-abc123.js"])
        rc, out = self.run_gate(files)
        self.assertEqual(EXIT_VIOLATION, rc, out)

    def test_the_byte_scan_does_not_fire_on_an_ordinary_bundle(self) -> None:
        """Both halves. A scan that flagged every JS file would satisfy the three tests above and
        fail every real build — only this one tells them apart."""
        files = self.clean_dist()
        files["assets/index-abc123.js"] = (
            b"const sourceMappingURL='not a directive';console.log(sourceMappingURL)"
        )
        rc, out = self.run_gate(files)
        self.assertEqual(EXIT_OK, rc, out)

    def test_a_dotenv_file_is_a_violation(self) -> None:
        files = self.clean_dist()
        # Content is irrelevant — the rule matches on the FILENAME. Deliberately not shaped like a
        # key=value assignment: the review gate's credential classifier hard-stopped this PR on the
        # first attempt, on a fixture belonging to the very gate whose job is to ban these files.
        files[".env.production"] = b"placeholder\n"
        rc, out = self.run_gate(files)
        self.assertEqual(EXIT_VIOLATION, rc, out)

    def test_a_missing_required_path_is_a_violation(self) -> None:
        files = self.clean_dist()
        del files[".well-known/security.txt"]
        rc, out = self.run_gate(files)
        self.assertEqual(EXIT_VIOLATION, rc, out)
        self.assertIn("security.txt", out)

    def test_an_undeclared_extension_is_a_violation(self) -> None:
        """The audit's 'new public artifact type' rule, implemented as an ALLOWLIST.

        A denylist is open-by-default: it can only ban the types someone thought of.
        """
        files = self.clean_dist()
        files["report.pdf"] = b"%PDF-1.4"
        rc, out = self.run_gate(files)
        self.assertEqual(EXIT_VIOLATION, rc, out)
        self.assertIn(".pdf", out)

    def test_hashed_filenames_do_not_churn_the_gate(self) -> None:
        """The counterpart to the test above: a rebuild changes every asset filename.

        A path-based rule would fail on every build. The type-based rule must not.
        """
        files = self.clean_dist()
        del files["assets/index-abc123.js"]
        files["assets/index-Zk9qLm42.js"] = b"console.log(2)"
        rc, out = self.run_gate(files)
        self.assertEqual(EXIT_OK, rc, out)

    # --- 5a: image freeze by content hash -------------------------------------------------

    def test_an_image_outside_the_frozen_inventory_is_a_violation(self) -> None:
        png = b"\x89PNG\r\n\x1a\nfake"
        files = self.clean_dist()
        files["assets/leaked-DkoP.png"] = png
        baseline = {**BASELINE, "image_inventory": {"mode": "frozen", "entries": []}}
        rc, out = self.run_gate(files, baseline)
        self.assertEqual(EXIT_VIOLATION, rc, out)
        self.assertIn("frozen inventory", out)

    def test_changed_image_bytes_fail_at_the_same_path(self) -> None:
        """website#470 shipped client data at ordinary paths. The bytes were the leak.

        This is the assertion that makes the inventory worth having: comparing paths would pass.
        """
        original = b"\x89PNG\r\n\x1a\noriginal"
        replaced = b"\x89PNG\r\n\x1a\nreplaced-with-client-data"
        baseline = {
            **BASELINE,
            "image_inventory": {
                "mode": "frozen",
                "entries": [{"path": "assets/logo.png", "sha256": digest(original), "reason": "brand"}],
            },
        }
        ok, _ = self.run_gate({**self.clean_dist(), "assets/logo.png": original}, baseline)
        self.assertEqual(EXIT_OK, ok)

        rc, out = self.run_gate({**self.clean_dist(), "assets/logo.png": replaced}, baseline)
        self.assertEqual(EXIT_VIOLATION, rc, out)

    def test_the_freeze_also_covers_package_sources(self) -> None:
        """website#470 lived in packages/*/src/**/assets before it was ever bundled.

        Guarding only `dist/` catches it one step too late — npm republication had already carried
        it into every tenant web repo.
        """
        png = b"\x89PNG\r\n\x1a\nguide-screenshot"
        baseline = {**BASELINE, "image_inventory": {"mode": "frozen", "entries": []}}
        rc, out = self.run_gate(
            self.clean_dist(), baseline, extra={"fb-creatives-ui/src/assets/guide.png": png}
        )
        self.assertEqual(EXIT_VIOLATION, rc, out)
        self.assertIn("guide.png", out)

    # --- cannot conclude (exit 2) ---------------------------------------------------------

    def test_an_empty_dist_is_exit_2_not_exit_0(self) -> None:
        """The single most important test here.

        A gate that passes over an empty directory passes over every build that produced nothing.
        """
        rc, out = self.run_gate({})
        self.assertEqual(EXIT_CANNOT_CONCLUDE, rc, out)
        self.assertNotEqual(EXIT_OK, rc)

    def test_a_dist_without_index_html_is_exit_2(self) -> None:
        rc, out = self.run_gate({"robots.txt": b"x", ".well-known/security.txt": b"y", "a.js": b"z"})
        self.assertEqual(EXIT_CANNOT_CONCLUDE, rc, out)

    def test_a_dist_below_the_file_floor_is_exit_2(self) -> None:
        baseline = {**BASELINE, "min_files": 500}
        rc, out = self.run_gate(self.clean_dist(), baseline)
        self.assertEqual(EXIT_CANNOT_CONCLUDE, rc, out)
        self.assertIn("floor", out)

    def test_a_baseline_entry_without_a_reason_is_exit_2(self) -> None:
        baseline = {
            **BASELINE,
            "banned_globs": [{"glob": "**/*.map", "permanent": True}],
        }
        rc, out = self.run_gate(self.clean_dist(), baseline)
        self.assertEqual(EXIT_CANNOT_CONCLUDE, rc, out)

    def test_a_baseline_entry_must_be_dated_or_permanent_but_not_both(self) -> None:
        """Neither, or both, is an unreviewable exception — the F-002 failure mode."""
        for entry in (
            {"glob": "**/*.map", "reason": "x"},
            {"glob": "**/*.map", "reason": "x", "permanent": True, "expires": "2027-01-01"},
        ):
            with self.subTest(entry=entry):
                rc, out = self.run_gate(self.clean_dist(), {**BASELINE, "banned_globs": [entry]})
                self.assertEqual(EXIT_CANNOT_CONCLUDE, rc, out)

    def test_config_errors_are_reported_before_the_artifact_is_read(self) -> None:
        """A malformed baseline must not be able to produce a per-file verdict.

        Driven by pointing the gate at a dist that would ALSO violate the contract: the exit code
        must be 2 (cannot conclude), never 1 (violation), because a verdict from a broken config
        means nothing in either direction.
        """
        files = {**self.clean_dist(), "assets/x.js.map": b"{}"}
        rc, out = self.run_gate(files, {**BASELINE, "min_files": "not-a-number"})
        self.assertEqual(EXIT_CANNOT_CONCLUDE, rc, out)


class SourceIsReviewableTests(unittest.TestCase):
    """Source that git treats as binary is source nobody reviews.

    This gate spent its whole first life unreviewed. `gate.mjs` carried two literal NUL bytes as a
    glob-expansion sentinel, which makes the entire file binary to git: `git diff` reported
    `Bin 0 -> 8332 bytes`, `--numstat` gave `-` for both counts, and all 202 lines were absent from
    every diff the review gate fetched. No engine ever saw a line of it.

    What made it expensive was the shape of the symptom. The PR reported
    `blocked_missing_authority` with `ACTIONABLE_FINDINGS_COUNT: unknown` — indistinguishable from a
    permissions problem, and it sat waiting for an authority grant that would have merged 202
    unreviewed lines.

    Bytes, not `git check-attr`: `.gitattributes` can force a diff for a file that is still binary
    in fact, which would make this pass while the reviewer still sees nothing.
    """

    TEXT_SUFFIXES = {".mjs", ".js", ".cjs", ".py", ".sh", ".json", ".yml", ".yaml", ".md", ".txt"}

    def test_no_tracked_source_file_contains_a_nul_byte(self) -> None:
        checked = 0
        offenders = []
        for directory in ("scripts", "tests"):
            root = REPO_ROOT / directory
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix not in self.TEXT_SUFFIXES:
                    continue
                checked += 1
                if b"\x00" in path.read_bytes():
                    offenders.append(str(path.relative_to(REPO_ROOT)))
        # A guard that scanned nothing would pass forever; assert it had material to work on.
        self.assertGreater(checked, 5, "scanned too few files to be a guard")
        self.assertEqual([], offenders, "these files are binary to git and cannot be reviewed")

    def test_the_gate_itself_is_text(self) -> None:
        """Named separately from the sweep above.

        If someone later narrows the sweep's directory list, the file this whole suite exists for
        must still be covered.
        """
        self.assertNotIn(b"\x00", GATE.read_bytes())


if __name__ == "__main__":
    unittest.main()
