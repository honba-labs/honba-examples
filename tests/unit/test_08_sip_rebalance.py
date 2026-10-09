"""Unit tests for Example 08 SIP investment + equal-weight rebalancing."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

from tests.synthetic import bar, weekdays

_PATH = Path(__file__).resolve().parents[2] / "universes" / "08_alpha30_union_ewr_backtest.py"


@pytest.fixture(scope="module")
def ex():
    spec = importlib.util.spec_from_file_location("ex08_sip", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_duration_to_days(ex) -> None:
    parse = ex.parse_duration_to_days
    assert parse(None) is None
    assert parse("") is None
    assert parse(180) == 180
    assert parse("180") == 180
    assert parse("180d") == 180
    assert parse("180days") == 180
    assert parse("6m") == 180
    assert parse("6mo") == 180
    assert parse("6months") == 180
    assert parse("1y") == 365
    assert parse("1year") == 365
    assert parse("2026-06-30", start_date=dt.date(2026, 1, 1)) == 180

    with pytest.raises(ValueError):
        parse(-5)
    with pytest.raises(ValueError):
        parse("invalid_duration")


def test_sip_with_no_of_sip(ex) -> None:
    """Verifies that SIP injections occur on rebalance days up to no_of_sip."""
    days = weekdays(dt.date(2026, 6, 1), 16)
    bars = []
    for d in days:
        bars.append(bar("AAA", d, 100.0))
        bars.append(bar("BBB", d, 100.0))

    initial_corpus = 100_000.0
    sip_amt = 10_000.0
    no_of_sip = 2
    rebalance_days = 3

    result = ex.simulate(
        bars,
        ["AAA", "BBB"],
        start=days[0],
        capital=initial_corpus,
        rebalance_days=rebalance_days,
        sip_amount=sip_amt,
        no_of_sip=no_of_sip,
    )

    assert result.initial_corpus == initial_corpus
    assert result.sip_amount == sip_amt
    assert result.sips_executed == 2
    assert result.total_invested == initial_corpus + 2 * sip_amt

    # Rebalances: day 0 has no SIP injected, first 2 rebalance sessions have sip_injected
    rebal_events = result.rebalances
    assert rebal_events[0]["sip_injected"] == 0.0
    assert rebal_events[1]["sip_injected"] == sip_amt
    assert rebal_events[2]["sip_injected"] == sip_amt
    # 3rd rebalance has 0.0 injected because no_of_sip=2 limit was reached
    if len(rebal_events) > 3:
        assert rebal_events[3]["sip_injected"] == 0.0

    # Metrics check
    assert result.metrics["total_invested"] == initial_corpus + 2 * sip_amt
    assert result.metrics["sips_executed"] == 2
    assert "net_profit" in result.metrics


def test_sip_with_duration(ex) -> None:
    """Verifies that SIP injections stop when the calendar duration expires."""
    days = weekdays(dt.date(2026, 6, 1), 16)
    bars = []
    for d in days:
        bars.append(bar("AAA", d, 100.0))
        bars.append(bar("BBB", d, 100.0))

    initial_corpus = 100_000.0
    sip_amt = 10_000.0
    # Allow SIP only within first 5 calendar days
    sip_duration_days = 5
    rebalance_days = 3

    result = ex.simulate(
        bars,
        ["AAA", "BBB"],
        start=days[0],
        capital=initial_corpus,
        rebalance_days=rebalance_days,
        sip_amount=sip_amt,
        sip_duration_days=sip_duration_days,
    )

    # 1st rebalance at day0 + 3 days is <= 5 days: gets SIP
    # 2nd rebalance at day0 + 6 days is > 5 days: does NOT get SIP
    assert result.sips_executed == 1
    assert result.total_invested == initial_corpus + sip_amt
    assert result.rebalances[1]["sip_injected"] == sip_amt
    if len(result.rebalances) > 2:
        assert result.rebalances[2]["sip_injected"] == 0.0


def test_cli_sip_arguments(ex) -> None:
    """Alpha30EWRExample correctly parses initial-corpus, sip, duration, and no-of-sip."""
    example = ex.Alpha30EWRExample()
    args = example.parse_args(
        [
            "--initial-corpus",
            "500000",
            "--sip",
            "25000",
            "--no-of-sip",
            "10",
            "--rebalance-days",
            "20",
        ]
    )
    assert args.initial_corpus == 500000.0
    assert args.sip == 25000.0
    assert args.no_of_sip == 10
    assert args.rebalance_days == 20
    assert example.initial_corpus == 500000.0
    assert example.sip == 25000.0
    assert example.no_of_sip == 10

    # Also test duration string
    example2 = ex.Alpha30EWRExample()
    args2 = example2.parse_args(
        [
            "--sip-amount",
            "15000",
            "--duration",
            "180d",
        ]
    )
    assert args2.sip == 15000.0
    assert args2.duration == "180d"
    assert example2.duration == "180d"


def test_time_weighted_return_handles_cash_inflow() -> None:
    """Cash inflow on a session does not create a fake positive return spike."""
    from honba_examples.metrics import curve_metrics

    # Scenario: 100k equity on day 1.
    # On day 2, 50k cash is injected, but asset prices did not move.
    # Equity becomes 150k.
    curve = [
        {"date": "2026-06-01", "value": 100_000.0, "cash": 0.0},
        {"date": "2026-06-02", "value": 150_000.0, "cash": 50_000.0},
    ]

    # Without cash_inflows adjustment, naive return would be +50%
    naive = curve_metrics(curve, capital=100_000.0)
    assert naive["total_return_pct"] == pytest.approx(50.0)

    # With cash_inflows, TWR return is 0.0% and total return on invested (150k) is 0.0%
    adjusted = curve_metrics(
        curve,
        capital=100_000.0,
        cash_inflows={"2026-06-02": 50_000.0},
        total_invested=150_000.0,
    )
    assert adjusted["total_return_pct"] == pytest.approx(0.0)
    assert adjusted["sharpe"] == 0.0
