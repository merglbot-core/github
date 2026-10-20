"""The cost alert issue is one carrier per month (merglbot-core/infra#3826, decision D-1)."""

from types import SimpleNamespace

from cost_monitoring import cli as cli_module

DATA = {"github": {"total_monthly_cost_usd": 1.0}, "gcp": {"total_net": 2.0, "currency": "CZK"}}
ALERTS = [{"scope": "gcp", "type": "total_cost", "currency": "CZK"}]


class FakeIssue:
    def __init__(self, number, title, pull_request=None):
        self.number = number
        self.title = title
        self.pull_request = pull_request
        self.comments = []

    def create_comment(self, body):
        self.comments.append(body)


class FakeRepo:
    def __init__(self, issues):
        self.issues = issues
        self.created = []
        self.queries = []

    def get_issues(self, state, labels):
        self.queries.append((state, tuple(labels)))
        return list(self.issues)

    def create_issue(self, title, body, labels):
        issue = FakeIssue(100 + len(self.created), title)
        self.created.append((title, body, labels))
        return issue


def _run(monkeypatch, repo):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(cli_module, "Github", lambda token: SimpleNamespace(get_repo=lambda name: repo))
    monkeypatch.setattr(cli_module, "format_alert_message", lambda alert: "alert")
    return cli_module.create_github_issue_for_alerts(ALERTS, "2026-10", DATA)


def test_existing_open_carrier_gets_a_comment_not_a_new_issue(monkeypatch):
    carrier = FakeIssue(1005, "Cost Threshold Exceeded - 2026-10")
    duplicate = FakeIssue(1013, "Cost Threshold Exceeded - 2026-10")
    repo = FakeRepo([duplicate, carrier, FakeIssue(965, "Cost Threshold Exceeded - 2026-09")])
    assert _run(monkeypatch, repo) is True
    assert repo.created == []
    assert len(carrier.comments) == 1 and duplicate.comments == []
    assert "daily snapshot" in carrier.comments[0]
    assert repo.queries == [("open", ("cost-monitoring",))]


def test_new_month_or_pull_request_with_the_title_creates_one_issue(monkeypatch):
    pr = FakeIssue(7, "Cost Threshold Exceeded - 2026-10", pull_request=object())
    repo = FakeRepo([pr, FakeIssue(965, "Cost Threshold Exceeded - 2026-09")])
    assert _run(monkeypatch, repo) is True
    assert [c[0] for c in repo.created] == ["Cost Threshold Exceeded - 2026-10"]
    assert pr.comments == []
