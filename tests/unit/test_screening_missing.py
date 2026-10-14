"""Unit: missing fundamentals fail the quality gates unless ``allow_missing`` is set."""

from __future__ import annotations

import datetime as dt

import pytest

from honba_examples.pipeline import FundamentalQualityFilter
from honba_examples.screening import graham, momentum, value
from tests.unit.conftest import SCREEN_ASOF

FY = dt.date(2024, 3, 31)


# ---- FundamentalQualityFilter -------------------------------------------------------------
def _kept(flt, rows):
    ctx = {"candidates": rows, "symbols": [r["symbol"] for r in rows]}
    return flt.run(ctx)["symbols"]


def test_quality_filter_fails_missing_values_by_default() -> None:
    rows = [
        {"symbol": "OK", "roce_pct": 20.0, "debt_to_equity": 0.5},
        {"symbol": "NOROCE", "roce_pct": None, "debt_to_equity": 0.5},
        {"symbol": "NODEBT", "roce_pct": 20.0, "debt_to_equity": None},
        {"symbol": "ABSENT", "debt_to_equity": 0.5},
        {
            "symbol": "NOMARGIN",
            "roce_pct": 20.0,
            "debt_to_equity": 0.5,
            "operating_margin_pct": None,
        },
        {"symbol": "MARGIN", "roce_pct": 20.0, "debt_to_equity": 0.5, "operating_margin_pct": 9.0},
    ]
    assert _kept(FundamentalQualityFilter(), rows) == ["OK", "MARGIN"]
    assert _kept(FundamentalQualityFilter(allow_missing=True), rows) == [
        "OK",
        "NOROCE",
        "NODEBT",
        "ABSENT",
        "NOMARGIN",
        "MARGIN",
    ]


def test_quality_filter_still_applies_thresholds_to_present_values() -> None:
    rows = [{"symbol": "LOW", "roce_pct": 1.0, "debt_to_equity": None}]
    assert _kept(FundamentalQualityFilter(allow_missing=True), rows) == []


def test_quality_filter_manifest_declares_allow_missing() -> None:
    spec = FundamentalQualityFilter.describe()["params"]["allow_missing"]
    assert spec["type"] == "bool" and spec["default"] is False
    assert "fail" in FundamentalQualityFilter.describe()["description"]


# ---- screeners ------------------------------------------------------------------------------
@pytest.fixture
def value_world(catalog_dir, add_series, fund, monkeypatch):
    monkeypatch.setattr(value, "find_duckdb_path", lambda: fund.path)
    for sym, roce, de in (("FULL", 20.0, 0.5), ("NOROCE", None, 0.5), ("NODEBT", 20.0, None)):
        add_series(sym, [100.0] * 300)
        fund.company(sym)
        fund.ratio(sym, FY, roce_pct=roce, debt_to_equity=de, eps=5.0, stockholders_equity=1e3)
        fund.stmt(sym, FY, {"Diluted Average Shares": 100.0})
    add_series("NOFUND", [100.0] * 300)  # no fundamentals rows at all
    return catalog_dir


def test_value_screener_missing_roce_and_debt_fail_by_default(value_world) -> None:
    def got(**kw):
        rows = value.screen_value_and_dividend(
            value_world, SCREEN_ASOF, train_years=2, max_pct_from_52w_low=50.0, **kw
        )
        return {r["symbol"] for r in rows}

    assert got() == {"FULL"}
    assert got(allow_missing=True) == {"FULL", "NOROCE", "NODEBT", "NOFUND"}


def test_multibagger_missing_values_fail_and_missing_roce_scores_zero(
    catalog_dir, add_series, fund, monkeypatch
) -> None:
    monkeypatch.setattr(momentum, "find_duckdb_path", lambda: fund.path)
    for sym, roce in (("ROCE10", 10.0), ("NOROCE", None)):
        add_series(sym, [100.0 * 1.001**i for i in range(300)])
        fund.ratio(sym, FY, roce_pct=roce, debt_to_equity=0.5)
    add_series("NOFUND", [100.0 * 1.001**i for i in range(300)])

    def got(**kw):
        rows = momentum.screen_multibaggers(
            catalog_dir, SCREEN_ASOF, train_years=2, min_return_1y=5.0, **kw
        )
        return {r["symbol"]: r for r in rows}

    strict = got()
    assert set(strict) == {"ROCE10", "NOROCE"}  # NOFUND: no debt/equity -> fails the gate
    loose = got(allow_missing=True)
    assert set(loose) == {"ROCE10", "NOROCE", "NOFUND"}
    # ROCE 10 earns 10 * 0.5 = 5 points; a missing ROCE earns none (it used to earn 5.0 too)
    assert strict["ROCE10"]["mb_score"] - strict["NOROCE"]["mb_score"] == pytest.approx(
        5.0, abs=0.11
    )


@pytest.fixture
def graham_world(catalog_dir, add_series, fund, monkeypatch):
    monkeypatch.setattr(graham, "find_duckdb_path", lambda: fund.path)
    full = {
        "Current Assets": 200.0,
        "Current Liabilities": 100.0,
        "Diluted Average Shares": 100.0,
        "Cash Dividends Paid": -10.0,
        "Total Liabilities Net Minority Interest": 100.0,
    }
    cases = {
        "FULL": (0.3, full),
        "NODEBT": (None, full),
        "NOCL": (0.3, {k: v for k, v in full.items() if k != "Current Liabilities"}),
    }
    for sym, (de, items) in cases.items():
        add_series(sym, [10.0] * 150)
        fund.ratio(sym, FY, eps=1.0, stockholders_equity=1000.0, debt_to_equity=de)
        fund.stmt(sym, FY, items)
    return catalog_dir


def test_intelligent_investor_missing_values_fail_by_default(graham_world) -> None:
    def got(**kw):
        rows = graham.screen_intelligent_investor(graham_world, SCREEN_ASOF, **kw)
        return {r["symbol"] for r in rows}

    assert got() == {"FULL"}
    assert got(allow_missing=True) == {"FULL", "NODEBT", "NOCL"}


def test_net_net_missing_values_fail_by_default(graham_world) -> None:
    def got(**kw):
        rows = graham.screen_net_net(graham_world, SCREEN_ASOF, max_price_to_ncav=None, **kw)
        return {r["symbol"] for r in rows}

    assert got() == {"FULL"}
    assert got(allow_missing=True) == {"FULL", "NODEBT", "NOCL"}
