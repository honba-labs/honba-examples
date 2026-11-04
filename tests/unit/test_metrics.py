"""Shared equity-curve metrics (one copy for 07 and 08)."""

from __future__ import annotations

import datetime as dt
import math

import pytest

from honba_examples.metrics import SESSIONS_PER_YEAR, curve_metrics, exposure_metrics

CURVE = [
    {"date": "2026-01-01", "value": 100.0, "cash": 10.0},
    {"date": "2026-01-02", "value": 110.0, "cash": 10.0},
    {"date": "2026-01-05", "value": 99.0, "cash": 20.0},
    {"date": "2026-01-06", "value": 121.0, "cash": 0.0},
]


def test_curve_metrics_golden() -> None:
    m = curve_metrics(CURVE, capital=100.0)
    rets = [0.10, -0.10, 22.0 / 99.0]
    mean = sum(rets) / 3
    std = math.sqrt(sum((r - mean) ** 2 for r in rets) / 2)
    years = 5 / 365.25
    assert m["final_value"] == 121.0
    assert m["total_return_pct"] == pytest.approx(21.0)
    assert m["cagr_pct"] == pytest.approx(100 * (1.21 ** (1 / years) - 1))
    assert m["max_drawdown_pct"] == pytest.approx(-10.0)
    assert m["sharpe"] == pytest.approx(mean / std * math.sqrt(SESSIONS_PER_YEAR))


def test_single_session_curve_is_finite() -> None:
    m = curve_metrics(CURVE[:1], capital=100.0)
    assert m["sharpe"] == 0.0
    assert m["max_drawdown_pct"] == 0.0
    assert math.isfinite(m["cagr_pct"])


def test_flat_curve_has_zero_sharpe() -> None:
    flat = [{"date": f"2026-01-0{i}", "value": 50.0, "cash": 50.0} for i in range(1, 5)]
    assert curve_metrics(flat, capital=50.0)["sharpe"] == 0.0


def test_empty_curve_is_rejected() -> None:
    with pytest.raises(ValueError):
        curve_metrics([], capital=100.0)


def test_cash_inflow_cagr_understates_the_money_weighted_return() -> None:
    """``cagr_pct`` treats total_invested as a day-0 lump sum, so with staggered SIP
    contributions it reads far below the true (money-weighted) annualized return;
    ``xirr_pct`` is the one that should reconcile with ``total_return_pct``."""
    day0 = dt.date(2025, 1, 1)
    day1 = day0 + dt.timedelta(days=1461)  # exactly 4.0 years at /365.25
    day2 = day0 + dt.timedelta(days=2922)  # exactly 8.0 years at /365.25

    rate = 0.10
    sip = 1000.0
    capital = 1000.0
    final_value = (capital + sip / (1 + rate) ** 4) * (1 + rate) ** 8

    curve = [
        {"date": day0.isoformat(), "value": capital, "cash": capital},
        {"date": day1.isoformat(), "value": capital + sip, "cash": capital + sip},
        {"date": day2.isoformat(), "value": final_value, "cash": final_value},
    ]
    total_invested = capital + sip

    m = curve_metrics(
        curve,
        capital=capital,
        cash_inflows={day1: sip},
        total_invested=total_invested,
    )

    assert m["xirr_pct"] == pytest.approx(100 * rate, rel=1e-3)
    # The naive lump-sum CAGR is well below the true money-weighted rate.
    assert m["cagr_pct"] < m["xirr_pct"] - 1.0


def test_xirr_omitted_without_cash_inflows() -> None:
    assert "xirr_pct" not in curve_metrics(CURVE, capital=100.0)


def test_exposure_metrics() -> None:
    m = exposure_metrics(CURVE, traded_notional=215.0)
    mean_value = (100 + 110 + 99 + 121) / 4
    assert m["turnover"] == pytest.approx(215.0 / mean_value)
    assert m["avg_cash_pct"] == pytest.approx(100 * (10 / 100 + 10 / 110 + 20 / 99 + 0 / 121) / 4)
