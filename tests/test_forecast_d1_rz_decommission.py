from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "forecast-d1-readiness.yml"
)


def test_decommissioned_ruzovyslon_never_gets_a_readiness_lane_or_slack_scope():
    source = WORKFLOW.read_text()

    assert "guardrail_ruzovyslon" not in source
    assert '--include-tenant "ruzovyslon"' not in source
    assert "forecast-d1-readiness-out-ruzovyslon" not in source
    assert "scope=\"ruzovyslon\"" not in source


def test_broad_scopes_explicitly_exclude_decommissioned_ruzovyslon():
    source = WORKFLOW.read_text()

    assert source.count('--exclude-tenant "ruzovyslon"') == 2
    assert '- cron: "15 10 * * *"' not in source
