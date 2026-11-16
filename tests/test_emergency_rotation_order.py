"""Ordering and failure tests for scripts/emergency/rotate-credentials.sh (gsa#327).

The script is sourced with stub `gcloud`, `openssl` and `shred` binaries on PATH that only
log their arguments; no cloud call is made and no real value is generated.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/emergency/rotate-credentials.sh"

GCLOUD_STUB = textwrap.dedent("""\
    #!/usr/bin/env bash
    printf '%s\\n' "$*" >> "$STUB_LOG"
    case "$1 $2 $3" in
      "config get-value project") printf '%s\\n' "${STUB_CONFIG_PROJECT:-}" ;;
      "secrets versions list") printf '%s\\n' "${STUB_CURRENT-projects/p/secrets/s/versions/1}" ;;
      "secrets versions add")
        [ -n "${STUB_FAIL_ADD:-}" ] && exit 1
        printf '%s\\n' "projects/p/secrets/s/versions/2" ;;
      "secrets versions describe") printf '%s\\n' "${STUB_STATE:-ENABLED}" ;;
      "secrets versions disable")
        [ -n "${STUB_FAIL_DISABLE:-}" ] && exit 1
        if [ -n "${STUB_TERM_AFTER_DISABLE:-}" ]; then kill -TERM "$PPID"; fi ;;
      "secrets versions enable") : ;;
    esac
    exit 0
    """)

OPENSSL_STUB = textwrap.dedent("""\
    #!/usr/bin/env bash
    printf 'openssl %s\\n' "$*" >> "$STUB_LOG"
    [ -n "${STUB_FAIL_OPENSSL:-}" ] && exit 1
    printf 'neutral\\n'
    """)

SHRED_STUB = "#!/usr/bin/env bash\nrm -f \"${@: -1}\"\n"


class RotationOrderTests(unittest.TestCase):
    def run_rotation(self, **env):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            for name, body in (("gcloud", GCLOUD_STUB), ("openssl", OPENSSL_STUB), ("shred", SHRED_STUB)):
                path = bin_dir / name
                path.write_text(body)
                path.chmod(0o755)
            log = Path(tmp) / "calls.log"
            log.touch()
            run_env = {
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "HOME": tmp,
                "TMPDIR": tmp,
                "STUB_LOG": str(log),
                "INCIDENT_LOG": str(Path(tmp) / "incidents.md"),
                "GCP_PROJECT": "merglbot-test-proj",
            }
            run_env.update(env)
            result = subprocess.run(
                ["bash", "-c", 'set -euo pipefail; source "$1"; rotate_gcp_secret demo-secret', "bash", str(SCRIPT)],
                stdin=subprocess.DEVNULL,
                env=run_env, capture_output=True, text=True, timeout=30)
            calls = [line for line in log.read_text().splitlines() if line]
            leftovers = list(Path(tmp).glob("rotate_secret_dir.*"))
        return result, calls, leftovers

    @staticmethod
    def verbs(calls):
        return [c.split()[2] for c in calls if c.startswith("secrets versions ")]

    def test_add_and_verify_before_disable(self):
        result, calls, leftovers = self.run_rotation()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.verbs(calls), ["list", "add", "describe", "disable"])
        self.assertTrue(all("--project=merglbot-test-proj" in c for c in calls if c.startswith("secrets ")))
        self.assertEqual(leftovers, [], "temporary value directory must be removed")
        self.assertNotIn("neutral", result.stdout + result.stderr)

    def test_failed_add_never_disables(self):
        result, calls, leftovers = self.run_rotation(STUB_FAIL_ADD="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.verbs(calls), ["list", "add"])
        self.assertEqual(leftovers, [])

    def test_failed_value_generation_makes_no_mutating_call(self):
        result, calls, _ = self.run_rotation(STUB_FAIL_OPENSSL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.verbs(calls), ["list"])

    def test_new_version_not_enabled_keeps_previous(self):
        result, calls, _ = self.run_rotation(STUB_STATE="DISABLED")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.verbs(calls), ["list", "add", "describe"])

    def test_failed_disable_keeps_new_version(self):
        result, calls, _ = self.run_rotation(STUB_FAIL_DISABLE="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.verbs(calls), ["list", "add", "describe", "disable"])
        self.assertIn("both versions stay enabled", result.stdout + result.stderr)

    def test_interrupt_after_disable_reenables_previous(self):
        result, calls, _ = self.run_rotation(STUB_TERM_AFTER_DISABLE="1")
        self.assertEqual(result.returncode, 130, result.stderr)
        self.assertEqual(self.verbs(calls), ["list", "add", "describe", "disable", "enable"])
        self.assertIn("secrets versions enable projects/p/secrets/s/versions/1", calls[-1])

    def test_no_enabled_version_adds_without_disable(self):
        result, calls, _ = self.run_rotation(STUB_CURRENT="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.verbs(calls), ["list", "add", "describe"])

    def test_missing_project_makes_no_secret_call(self):
        result, calls, _ = self.run_rotation(GCP_PROJECT="")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.verbs(calls), [])

    def test_invalid_name_makes_no_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                ["bash", "-c", 'source "$1"; rotate_gcp_secret "a;b"', "bash", str(SCRIPT)],
                stdin=subprocess.DEVNULL,
                env={"PATH": "/usr/bin:/bin", "INCIDENT_LOG": os.path.join(tmp, "i.md")},
                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid secret name", result.stdout)


if __name__ == "__main__":
    unittest.main()
