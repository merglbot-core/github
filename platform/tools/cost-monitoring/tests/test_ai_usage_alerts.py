import pytest


from cost_monitoring.utils.anomaly_config import parse_daily_spike_factor


def test_daily_spike_pct_fraction() -> None:
    assert parse_daily_spike_factor({"daily_spike_pct": 0.5}) == pytest.approx(1.5)


def test_daily_spike_pct_fraction_one() -> None:
    assert parse_daily_spike_factor({"daily_spike_pct": 1.0}) == pytest.approx(2.0)


def test_daily_spike_pct_integer_percent() -> None:
    assert parse_daily_spike_factor({"daily_spike_pct": 50}) == pytest.approx(1.5)
    assert parse_daily_spike_factor({"daily_spike_pct": 150}) == pytest.approx(2.5)


def test_daily_spike_pct_invalid_warns_and_defaults(capsys: pytest.CaptureFixture[str]) -> None:
    assert parse_daily_spike_factor({"daily_spike_pct": 1.01}) == pytest.approx(2.0)
    captured = capsys.readouterr()
    assert "WARN:" in captured.err


def test_daily_spike_factor_direct() -> None:
    assert parse_daily_spike_factor({"daily_spike_factor": 3.0}) == pytest.approx(3.0)
