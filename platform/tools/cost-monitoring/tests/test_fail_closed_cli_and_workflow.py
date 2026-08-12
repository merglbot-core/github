from pathlib import Path

import yaml
from click.testing import CliRunner

from cost_monitoring import cli as cli_module


def write_configs(tmp_path):
    settings = tmp_path / "settings.yml"
    thresholds = tmp_path / "thresholds.yml"
    settings.write_text(
        yaml.safe_dump(
            {
                "github": {"enterprise": "example", "orgs": ["example"], "pricing": {}},
                "gcp": {"billing_export": {"project_id": "billing-project"}, "projects": {"core": ["p"]}},
            }
        ),
        encoding="utf-8",
    )
    thresholds.write_text(
        yaml.safe_dump({"github": {"copilot": {}}, "gcp": {"currency": "CZK", "defaults": {}}}),
        encoding="utf-8",
    )
    return settings, thresholds


def test_soft_fail_does_not_hide_collection_errors(monkeypatch, tmp_path):
    settings, thresholds = write_configs(tmp_path)
    monkeypatch.setattr(cli_module, "collect_github", lambda *args, **kwargs: {"total_monthly_cost_usd": 0})

    def fail_gcp(*args, **kwargs):
        raise RuntimeError("query failed")

    monkeypatch.setattr(cli_module, "collect_gcp", fail_gcp)

    result = CliRunner().invoke(
        cli_module.cli,
        [
            "generate",
            "--config",
            str(settings),
            "--thresholds",
            str(thresholds),
            "--outdir",
            str(tmp_path / "reports"),
            "--dry-run",
            "--soft-fail",
        ],
    )

    assert result.exit_code == 1
    assert "Fatal cost-monitoring error (RuntimeError)" in result.output
    assert "query failed" not in result.output


def test_soft_fail_applies_only_to_threshold_alerts(monkeypatch, tmp_path):
    settings, thresholds = write_configs(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "collect_github",
        lambda *args, **kwargs: {
            "total_monthly_cost_usd": 0,
            "copilot": {},
            "enterprise_cloud": {},
            "total_members": 0,
        },
    )
    monkeypatch.setattr(
        cli_module,
        "collect_gcp",
        lambda *args, **kwargs: {
            "currency": "CZK",
            "projects_monitored": 1,
            "total_cost": 1,
            "total_credits": 0,
            "total_net": 1,
            "project_costs": [],
        },
    )
    monkeypatch.setattr(
        cli_module,
        "evaluate_all_thresholds",
        lambda *args, **kwargs: {
            "alerts": [{"scope": "gcp", "type": "total_cost", "currency": "CZK"}],
            "threshold_exceeded": True,
            "high_count": 0,
            "medium_count": 1,
        },
    )
    monkeypatch.setattr(cli_module, "write_all_reports", lambda *args, **kwargs: {})
    notification_calls = []

    def record_notification(*args, **kwargs):
        notification_calls.append((args, kwargs))
        return True

    monkeypatch.setattr(cli_module, "send_cost_report_to_slack", record_notification)
    monkeypatch.setattr(cli_module, "create_github_issue_for_alerts", record_notification)

    result = CliRunner().invoke(
        cli_module.cli,
        [
            "generate",
            "--config",
            str(settings),
            "--thresholds",
            str(thresholds),
            "--outdir",
            str(tmp_path / "reports"),
            "--dry-run",
            "--soft-fail",
        ],
    )

    assert result.exit_code == 0
    assert notification_calls == []


def test_workflow_does_not_blanket_suppress_generate_failures():
    repo_root = Path(__file__).resolve().parents[4]
    workflow = (repo_root / ".github/workflows/cost-monitoring.yml").read_text(encoding="utf-8")

    generate_block = workflow.split("- name: Generate cost report", 1)[1].split("- name: AI Usage Telemetry Alerts", 1)[
        0
    ]
    assert "continue-on-error" not in generate_block
    assert "--soft-fail" in generate_block
    assert "|| EXIT=" not in generate_block
    assert '"${CMD[@]}"' in generate_block


def test_workflow_dry_run_suppresses_separate_ai_usage_notifier():
    repo_root = Path(__file__).resolve().parents[4]
    workflow = (repo_root / ".github/workflows/cost-monitoring.yml").read_text(encoding="utf-8")

    ai_alert_block = workflow.split("- name: AI Usage Telemetry Alerts (Budgets)", 1)[1].split(
        "- name: Generate Step Summary", 1
    )[0]
    assert "if: github.event_name != 'workflow_dispatch' || inputs.dry_run != true" in ai_alert_block
