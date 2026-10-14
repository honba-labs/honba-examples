"""Unit: fiscal-lag cutoff and one consistent fiscal_date per symbol across ratios/statements."""

from __future__ import annotations

import datetime as dt

import pytest

from honba_examples.pipeline import DividendYieldScreener, MultibaggerScreener, ValueScreener
from honba_examples.pipeline.reporting import add_report_columns
from honba_examples.screening import graham, momentum, value
from honba_examples.screening.common import DEFAULT_FISCAL_LAG_DAYS
from honba_examples.screening.dispatch import run_screener
from tests.unit.conftest import SCREEN_ASOF

RECENT = SCREEN_ASOF - dt.timedelta(days=30)  # filed "after" a 90-day lag would allow
OLD = SCREEN_ASOF - dt.timedelta(days=400)


def test_default_lag_is_ninety_days() -> None:
    assert DEFAULT_FISCAL_LAG_DAYS == 90


def _uptrend(n=300):
    return [100.0 * 1.001**i for i in range(n)]


def test_multibagger_applies_fiscal_lag(catalog_dir, add_series, fund, monkeypatch) -> None:
    monkeypatch.setattr(momentum, "find_duckdb_path", lambda: fund.path)
    add_series("X", _uptrend())
    fund.ratio("X", OLD, debt_to_equity=5.0)  # too levered
    fund.ratio("X", RECENT, debt_to_equity=0.5)  # fine, but only visible without a lag

    def got(**kw):
        rows = momentum.screen_multibaggers(
            catalog_dir, SCREEN_ASOF, train_years=2, min_return_1y=5.0, **kw
        )
        return [r["symbol"] for r in rows]

    assert got(fiscal_lag_days=0) == ["X"]
    assert got() == []  # default 90 days: only the old, over-levered row is visible
    assert got(fiscal_lag_days=20) == ["X"]


def test_value_uses_one_fiscal_date_for_eps_and_shares(
    catalog_dir, add_series, fund, monkeypatch
) -> None:
    monkeypatch.setattr(value, "find_duckdb_path", lambda: fund.path)
    fy23, fy24 = dt.date(2022, 12, 31), dt.date(2023, 12, 31)
    for sym in ("SAME", "SKEW"):
        add_series(sym, [100.0] * 300)
        fund.ratio(sym, fy24, roce_pct=20.0, debt_to_equity=0.5, eps=5.0, stockholders_equity=1e3)
    fund.stmt("SAME", fy24, {"Diluted Average Shares": 100.0})
    fund.stmt("SKEW", fy23, {"Diluted Average Shares": 100.0})  # shares only from the older year
    rows = {
        r["symbol"]: r
        for r in value.screen_value_and_dividend(
            catalog_dir, SCREEN_ASOF, train_years=2, max_pct_from_52w_low=50.0
        )
    }
    assert rows["SAME"]["pb_ratio"] == pytest.approx(10.0)  # 100 / (1000 / 100)
    assert rows["SKEW"]["pe_ratio"] == pytest.approx(20.0)  # EPS needs only the ratios row
    assert rows["SKEW"]["pb_ratio"] is None  # no mixing FY2023 shares with FY2022 equity


def test_net_net_ratios_follow_balance_sheet_date(
    catalog_dir, add_series, fund, monkeypatch
) -> None:
    monkeypatch.setattr(graham, "find_duckdb_path", lambda: fund.path)
    fy23, fy24 = dt.date(2022, 12, 31), dt.date(2023, 12, 31)
    add_series("N", [40.0] * 150)
    bs = {
        "Current Assets": 1000.0,
        "Current Liabilities": 100.0,
        "Total Liabilities Net Minority Interest": 200.0,
        "Diluted Average Shares": 10.0,
    }
    fund.stmt("N", fy23, bs)  # balance sheet only from the older year
    fund.ratio("N", fy24, debt_to_equity=0.3)  # ratios from a later year: must not be borrowed
    rows = graham.screen_net_net(catalog_dir, SCREEN_ASOF, allow_missing=True)
    assert [r["symbol"] for r in rows] == ["N"] and rows[0]["debt_to_equity"] is None


def test_report_columns_respect_fiscal_lag(
    fund,
) -> None:
    fund.ratio("R", RECENT, roce_pct=40.0)
    fund.ratio("R", OLD, roce_pct=10.0)
    cands = [{"symbol": "R", "close": 10.0}]
    default = add_report_columns(cands, ["roce_pct"], SCREEN_ASOF, db_path=fund.path)
    assert default[0]["roce_pct"] == 10.0
    no_lag = add_report_columns(
        cands, ["roce_pct"], SCREEN_ASOF, db_path=fund.path, fiscal_lag_days=0
    )
    assert no_lag[0]["roce_pct"] == 40.0


def test_run_screener_and_steps_expose_fiscal_lag(
    catalog_dir, add_series, fund, monkeypatch
) -> None:
    monkeypatch.setattr(momentum, "find_duckdb_path", lambda: fund.path)
    add_series("RELIANCE", _uptrend())
    fund.ratio("RELIANCE", RECENT, debt_to_equity=0.5)
    args = ("multibagger", catalog_dir, SCREEN_ASOF, 2, 10)
    assert run_screener(*args, allow_missing=True) != []
    assert run_screener(*args) == []  # lag hides the only row -> missing -> fails
    assert [r["symbol"] for r in run_screener(*args, fiscal_lag_days=0)] == ["RELIANCE"]
    for cls in (MultibaggerScreener, ValueScreener, DividendYieldScreener):
        spec = cls.describe()["params"]["fiscal_lag_days"]
        assert (spec["default"], spec["min"]) == (90, 0) and spec["max"] >= 365
        assert cls().fiscal_lag_days == 90
    step = MultibaggerScreener(asof=SCREEN_ASOF, catalog_dir=catalog_dir, train_years=2)
    step.min_return_1y = 5.0
    assert step.run({})["symbols"] == []
    step.fiscal_lag_days = 0
    assert step.run({})["symbols"] == ["RELIANCE"]
