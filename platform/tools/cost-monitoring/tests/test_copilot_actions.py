import json
import os

import google.auth
import pytest
import yaml
from click.testing import CliRunner
from test_copilot_identity import ACCOUNT, CONTEXT, FILE_ACCOUNT, ORIGIN, PROVIDER, SUBJECT
from test_copilot_identity import identity_wire as identity_wire

from cost_monitoring import cli as cli_module
from cost_monitoring.monitor import github_monitor
from cost_monitoring.monitor.copilot_actions import load_actions_reader


def configuration():
    return {
        "version": 1,
        "origin": ORIGIN,
        "identity": {
            "service_account": ACCOUNT,
            "credential_file_service_account": FILE_ACCOUNT,
            "subject": SUBJECT,
            "provider": PROVIDER,
            **{key: value for key, value in CONTEXT.items() if key != "event_name"},
        },
        "targets": [
            {"organization_id": 101, "organization_login": "core", "grant_id": "core-total", "target_sha256": "0" * 64}
        ],
    }


def prepare(fixture, path, monkeypatch):
    path.write_text(json.dumps(configuration()))
    monkeypatch.setenv("GITHUB_ACCESS_WIF_CONFIGURATION", str(fixture.file))
    fixture.broker_response = {"organization": {"id": 101, "login": "core"}, "seat_breakdown": {"total": 7}}


def test_actual_loader_google_auth_and_aggregate_route_without_adc(identity_wire, tmp_path, monkeypatch):
    f = identity_wire
    path = tmp_path / "broker.json"
    prepare(f, path, monkeypatch)
    monkeypatch.setattr(google.auth, "default", lambda *a, **k: pytest.fail("ADC discovery"))
    reader = load_actions_reader(path)
    assert f.calls == []
    assert reader("core") == {"seats_assigned": 7, "seats_purchased": 7}
    assert [call["route"] for call in f.calls] == ["actions", "sts", "iam", "broker"]
    assert all(call["auth_bound"] for call in f.calls)


@pytest.mark.parametrize("producer", [None, ACCOUNT, "not-an-account"])
def test_missing_invalid_or_shared_billing_identity_is_refused(identity_wire, tmp_path, monkeypatch, producer):
    f = identity_wire
    path = tmp_path / "broker.json"
    prepare(f, path, monkeypatch)
    value = configuration()
    if producer is None:
        del value["identity"]["credential_file_service_account"]
    else:
        value["identity"]["credential_file_service_account"] = producer
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="^copilot_actions_configuration_invalid$"):
        load_actions_reader(path)
    assert f.calls == []


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_ACTIONS", "false"),
        ("GITHUB_REPOSITORY", "other/repo"),
        ("GITHUB_REPOSITORY_ID", "43"),
        ("GITHUB_REPOSITORY_OWNER_ID", "102"),
        ("GITHUB_REF", "refs/pull/1/merge"),
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("GITHUB_WORKFLOW_REF", "other-workflow"),
    ],
)
def test_context_mismatch_refuses_before_identity_file_or_network(identity_wire, tmp_path, monkeypatch, key, value):
    f = identity_wire
    path = tmp_path / "broker.json"
    prepare(f, path, monkeypatch)
    monkeypatch.setenv(key, value)
    monkeypatch.setenv("GITHUB_ACCESS_WIF_CONFIGURATION", "/must/not/be/read")
    with pytest.raises(ValueError, match="^copilot_actions_configuration_invalid$"):
        load_actions_reader(path)
    assert f.calls == []


def test_duplicate_input_fields_fifo_and_symlink_are_refused(identity_wire, tmp_path, monkeypatch):
    f = identity_wire
    path = tmp_path / "broker.json"
    prepare(f, path, monkeypatch)
    path.write_bytes(b'{"version":1,"version":1}')
    with pytest.raises(ValueError):
        load_actions_reader(path)
    path.unlink()
    os.mkfifo(path)
    with pytest.raises(ValueError):
        load_actions_reader(path)
    path.unlink()
    path.symlink_to(f.file)
    with pytest.raises(ValueError):
        load_actions_reader(path)
    assert f.calls == []


def test_real_cli_generate_selects_workload_identity_and_never_personal_fallback(identity_wire, tmp_path, monkeypatch):
    f = identity_wire
    path = tmp_path / "broker.json"
    prepare(f, path, monkeypatch)
    settings, thresholds = tmp_path / "settings.yml", tmp_path / "thresholds.yml"
    settings.write_text(
        yaml.safe_dump({"github": {"enterprise": "example", "orgs": ["core"], "pricing": {}}, "gcp": {"fixture": True}})
    )
    thresholds.write_text("{}")
    monkeypatch.setattr(github_monitor, "_enterprise_token", lambda: pytest.fail("personal fallback"))
    monkeypatch.setattr(google.auth, "default", lambda *a, **k: pytest.fail("ADC discovery"))
    monkeypatch.setattr(
        cli_module,
        "collect_gcp",
        lambda *_: {
            "currency": "CZK",
            "projects_monitored": 1,
            "total_cost": 0,
            "total_credits": 0,
            "total_net": 0,
            "project_costs": [],
        },
    )
    monkeypatch.setattr(
        cli_module,
        "evaluate_all_thresholds",
        lambda *_: {"threshold_exceeded": False, "alerts": [], "high_count": 0, "medium_count": 0},
    )
    captured = {}

    def reports(_directory, data, _month):
        captured.update(data)
        return {}

    monkeypatch.setattr(cli_module, "write_all_reports", reports)
    monkeypatch.setattr(cli_module, "send_cost_report_to_slack", lambda *_: pytest.fail("notification"))
    monkeypatch.setattr(cli_module, "create_github_issue_for_alerts", lambda *_: pytest.fail("issue write"))
    result = CliRunner().invoke(
        cli_module.cli,
        [
            "generate",
            "--config",
            str(settings),
            "--thresholds",
            str(thresholds),
            "--github-broker-config",
            str(path),
            "--dry-run",
            "--soft-fail",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["github"]["copilot"]["seats_purchased"] == 7
    assert captured["github"]["total_monthly_cost_usd"] == 133
    assert [call["route"] for call in f.calls] == ["actions", "sts", "iam", "broker"]
