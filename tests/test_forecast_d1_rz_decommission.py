import unittest
from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "forecast-d1-readiness.yml"
)


class ForecastD1RzDecommissionTests(unittest.TestCase):
    """The CI gate runs `unittest discover`, which collects TestCase subclasses only.

    These checks used to be module-level functions, so the gate reported
    "Ran 0 tests" for this file and never executed them (same defect class as
    merglbot-core/infra#1712).
    """

    def test_decommissioned_ruzovyslon_never_gets_a_readiness_lane_or_slack_scope(self):
        source = WORKFLOW.read_text()

        assert "guardrail_ruzovyslon" not in source
        assert '--include-tenant "ruzovyslon"' not in source
        assert "forecast-d1-readiness-out-ruzovyslon" not in source
        assert "scope=\"ruzovyslon\"" not in source

    def test_broad_scopes_explicitly_exclude_decommissioned_ruzovyslon(self):
        source = WORKFLOW.read_text()

        assert source.count('--exclude-tenant "ruzovyslon"') == 2
        assert '- cron: "15 10 * * *"' not in source
