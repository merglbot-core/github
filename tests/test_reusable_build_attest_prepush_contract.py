"""Fail-closed contract for the reusable container publish workflow.

The cost-control invariant is intentionally textual and order-sensitive: the
Trivy gate must inspect a local-only image before the first registry push, and
production tags may move only after local/published config-digest parity.
"""

from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "reusable-build-attest.yml"


def step_position(text: str, name: str) -> int:
    marker = f"- name: {name}"
    position = text.find(marker)
    if position < 0:
        raise AssertionError(f"missing workflow step: {name}")
    return position


class ReusableBuildAttestPrePushContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_scan_precedes_any_candidate_push(self) -> None:
        local_build = step_position(self.text, "Build local image for pre-push scan")
        reclaim = step_position(self.text, "Reclaim BuildKit cache before Trivy")
        scan = step_position(self.text, "Run Trivy vulnerability scan")
        auth = step_position(self.text, "Authenticate to GCP")
        candidate = step_position(self.text, "Build final image and push isolated candidate")

        self.assertLess(local_build, reclaim)
        self.assertLess(reclaim, scan)
        self.assertLess(local_build, scan)
        self.assertLess(scan, auth)
        self.assertLess(auth, candidate)

        local_block = self.text[local_build:scan]
        self.assertIn("load: true", local_block)
        self.assertIn("push: false", local_block)
        self.assertIn("provenance: false", local_block)
        self.assertIn("sbom: false", local_block)

        reclaim_block = self.text[reclaim:scan]
        self.assertIn("if: inputs.scan", reclaim_block)
        self.assertIn("BUILDER_NAME: ${{ steps.buildx.outputs.name }}", reclaim_block)
        self.assertIn('if [ -z "$BUILDER_NAME" ]', reclaim_block)
        self.assertEqual(
            reclaim_block.count("docker image inspect --format '{{.Id}}'"),
            2,
        )
        self.assertIn(
            'docker buildx prune --builder "$BUILDER_NAME" --all --force',
            reclaim_block,
        )
        self.assertIn('if [ -z "$BEFORE_ID" ] || [ "$BEFORE_ID" != "$AFTER_ID" ]', reclaim_block)

        scan_block = self.text[scan:step_position(self.text, "Upload Trivy SARIF")]
        self.assertIn("image-ref: ${{ steps.refs.outputs.local_ref }}", scan_block)
        self.assertIn("exit-code: '1'", scan_block)
        self.assertNotIn("continue-on-error", scan_block)

        sarif_block = self.text[
            step_position(self.text, "Upload Trivy SARIF"):
            step_position(self.text, "Validate WIF provider (SSOT)")
        ]
        self.assertIn("inputs.scan && always()", sarif_block)
        self.assertIn("hashFiles('trivy-results.sarif') != ''", sarif_block)

    def test_buildx_builder_is_addressable_for_scoped_cache_reclaim(self) -> None:
        setup = step_position(self.text, "Set up Docker Buildx")
        local_build = step_position(self.text, "Build local image for pre-push scan")
        setup_block = self.text[setup:local_build]

        self.assertIn("id: buildx", setup_block)
        self.assertIn("driver: docker-container", setup_block)
        self.assertNotIn("docker system prune", self.text)
        self.assertNotIn("docker image prune", self.text)

    def test_candidate_is_isolated_until_digest_parity(self) -> None:
        epoch = step_position(self.text, "Resolve reproducible build epoch")
        local_build = step_position(self.text, "Build local image for pre-push scan")
        candidate = step_position(self.text, "Build final image and push isolated candidate")
        parity = step_position(self.text, "Verify candidate matches the locally scanned image")
        promotion = step_position(self.text, "Promote verified production tags")
        provenance = step_position(self.text, "Generate SLSA Provenance")

        self.assertLess(epoch, local_build)
        self.assertLess(local_build, candidate)
        self.assertLess(candidate, parity)
        self.assertLess(parity, promotion)
        self.assertLess(promotion, provenance)

        epoch_block = self.text[epoch:local_build]
        self.assertIn('git log -1 --pretty=%ct', epoch_block)
        self.assertIn('id: build_epoch', epoch_block)
        self.assertIn('>> "$GITHUB_OUTPUT"', epoch_block)

        local_block = self.text[local_build:step_position(self.text, "Reclaim BuildKit cache before Trivy")]
        self.assertIn("SOURCE_DATE_EPOCH: ${{ steps.build_epoch.outputs.value }}", local_block)

        candidate_block = self.text[candidate:parity]
        self.assertIn("tags: ${{ steps.refs.outputs.candidate_ref }}", candidate_block)
        self.assertNotIn("tags: ${{ steps.meta.outputs.tags }}", candidate_block)
        self.assertIn("SOURCE_DATE_EPOCH: ${{ steps.build_epoch.outputs.value }}", candidate_block)
        self.assertIn("provenance: true", candidate_block)
        self.assertIn("sbom: true", candidate_block)

        parity_block = self.text[parity:promotion]
        self.assertIn("LOCAL_CONFIG_DIGEST", parity_block)
        self.assertIn("PUBLISHED_CONFIG_DIGEST", parity_block)
        self.assertIn('if [ "$LOCAL_CONFIG_DIGEST" != "$PUBLISHED_CONFIG_DIGEST" ]', parity_block)

        promotion_block = self.text[promotion:provenance]
        self.assertIn("docker buildx imagetools create", promotion_block)
        self.assertIn("EXPECTED_CONFIG_DIGEST", promotion_block)
        self.assertIn("TAG_ROOT_DIGEST", promotion_block)
        self.assertIn('if [ "$TAG_ROOT_DIGEST" != "$CANDIDATE_DIGEST" ]', promotion_block)

    def test_security_outputs_and_attestation_remain_wired(self) -> None:
        self.assertIn("security-events: write", self.text)
        self.assertIn("github/codeql-action/upload-sarif@", self.text)
        self.assertIn("Create Binary Authorization Attestation", self.text)
        self.assertIn("Upload SLSA Provenance", self.text)
        self.assertIn("if: inputs.attest && inputs.push", self.text)


if __name__ == "__main__":
    unittest.main()
