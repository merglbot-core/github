import pytest

from cost_monitoring.monitor import github_monitor


def test_collect_github_requires_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "run-token-must-not-be-used-for-enterprise-reads")
    monkeypatch.delenv("ENTERPRISE_GITHUB_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="Missing ENTERPRISE_GITHUB_TOKEN"):
        github_monitor.collect_github("example", ["example"], {})


def test_enterprise_headers_do_not_use_run_token(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_GITHUB_TOKEN", "enterprise-read-token")
    monkeypatch.setenv("GITHUB_TOKEN", "run-token-for-issues-only")

    headers = github_monitor._headers()

    assert headers["Authorization"] == "Bearer enterprise-read-token"
    assert "run-token-for-issues-only" not in headers["Authorization"]
    assert headers["X-GitHub-Api-Version"] == "2026-03-10"


class FakeResponse:
    def __init__(self, status_code, payload=None, json_error=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


def test_copilot_org_parses_current_aggregate_schema(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_GITHUB_TOKEN", "enterprise-read-token")
    requested_urls = []

    def fake_get(url, **kwargs):
        requested_urls.append(url)
        return FakeResponse(200, {"seat_breakdown": {"total": 7, "active_this_cycle": 6}})

    monkeypatch.setattr(github_monitor.requests, "get", fake_get)

    assert github_monitor.get_copilot_org("example") == {"seats_assigned": 7, "seats_purchased": 7}
    assert requested_urls == ["https://api.github.com/orgs/example/copilot/billing"]
    assert all("/billing/seats" not in url for url in requested_urls)


def test_copilot_org_skips_only_not_found(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_GITHUB_TOKEN", "enterprise-read-token")
    monkeypatch.setattr(
        github_monitor.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(404, {"message": "must not be inspected"}),
    )

    assert github_monitor.get_copilot_org("example") == {}


@pytest.mark.parametrize("status", [401, 403])
def test_copilot_org_fails_closed_on_auth_error(monkeypatch, status):
    monkeypatch.setenv("ENTERPRISE_GITHUB_TOKEN", "enterprise-read-token")
    monkeypatch.setattr(
        github_monitor.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(status, {"message": "must not be inspected"}),
    )

    with pytest.raises(github_monitor.GitHubCopilotOrgAuthorizationError, match=rf"failed with status {status}"):
        github_monitor.get_copilot_org("example")


def test_copilot_org_fails_closed_on_billing_configuration_error(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_GITHUB_TOKEN", "enterprise-read-token")
    monkeypatch.setattr(
        github_monitor.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(422, {"message": "must not be inspected"}),
    )

    with pytest.raises(github_monitor.GitHubCopilotOrgConfigurationError, match="failed with status 422"):
        github_monitor.get_copilot_org("example")


def test_copilot_org_fails_closed_on_provider_error(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_GITHUB_TOKEN", "enterprise-read-token")
    monkeypatch.setattr(
        github_monitor.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(500, {"message": "must not be inspected"}),
    )

    with pytest.raises(github_monitor.GitHubCopilotOrgRequestError, match="failed with status 500"):
        github_monitor.get_copilot_org("example")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"seat_breakdown": {}},
        {"seat_breakdown": {"total": "7"}},
        {"seat_breakdown": {"total": -1}},
        {"seat_breakdown": {"total": True}},
    ],
)
def test_copilot_org_fails_closed_on_invalid_aggregate_schema(monkeypatch, payload):
    monkeypatch.setenv("ENTERPRISE_GITHUB_TOKEN", "enterprise-read-token")
    monkeypatch.setattr(github_monitor.requests, "get", lambda *args, **kwargs: FakeResponse(200, payload))

    with pytest.raises(github_monitor.GitHubCopilotOrgSchemaError, match="invalid aggregate seat schema"):
        github_monitor.get_copilot_org("example")


def test_copilot_org_fails_closed_on_invalid_json(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_GITHUB_TOKEN", "enterprise-read-token")
    monkeypatch.setattr(
        github_monitor.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(200, json_error=ValueError("raw parser detail")),
    )

    with pytest.raises(github_monitor.GitHubCopilotOrgSchemaError, match="response is not valid JSON") as exc_info:
        github_monitor.get_copilot_org("example")
    assert "raw parser detail" not in str(exc_info.value)


def test_collect_github_uses_org_aggregates_and_skips_unpriced_enterprise_endpoint(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_GITHUB_TOKEN", "enterprise-read-token")
    org_values = {"one": 2, "two": 5}
    monkeypatch.setattr(
        github_monitor,
        "get_copilot_org",
        lambda org: {"seats_assigned": org_values[org], "seats_purchased": org_values[org]},
    )

    def forbidden_enterprise_lookup(enterprise):
        raise AssertionError("unpriced Enterprise Cloud endpoint must not be queried")

    def forbidden_member_lookup(org):
        raise AssertionError("unpriced member census must not be queried")

    monkeypatch.setattr(github_monitor, "get_enterprise_cloud_seats", forbidden_enterprise_lookup)
    monkeypatch.setattr(github_monitor, "get_org_members_count", forbidden_member_lookup)

    result = github_monitor.collect_github(
        "example-enterprise",
        ["one", "two"],
        {"copilot_usd_per_seat": 19, "enterprise_cloud_usd_per_seat": 0},
    )

    assert result["copilot"] == {
        "seats_assigned": 7,
        "seats_purchased": 7,
        "monthly_cost_usd": 133.0,
        "price_per_seat": 19.0,
    }
    assert result["enterprise_cloud"]["seats"] == 0
    assert result["org_members"] == []
    assert result["total_members"] is None
    assert result["member_census_status"] == "not_collected_unpriced"


def test_collect_github_fails_closed_when_all_org_aggregates_unavailable(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_GITHUB_TOKEN", "enterprise-read-token")
    monkeypatch.setattr(github_monitor, "get_copilot_org", lambda org: {})

    with pytest.raises(
        github_monitor.GitHubCopilotAggregateUnavailableError,
        match="No GitHub Copilot billing source returned authoritative aggregate data",
    ):
        github_monitor.collect_github("example-enterprise", ["one", "two"], {})


def test_member_count_query_requests_aggregate_only(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_GITHUB_TOKEN", "enterprise-read-token")
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["query"] = kwargs["json"]["query"]
        return FakeResponse(200, {"data": {"organization": {"membersWithRole": {"totalCount": 11}}}})

    monkeypatch.setattr(github_monitor.requests, "post", fake_post)

    assert github_monitor.get_org_members_count("example") == 11
    assert captured["url"] == "https://api.github.com/graphql"
    assert "totalCount" in captured["query"]
    assert "nodes" not in captured["query"]
    assert "edges" not in captured["query"]
