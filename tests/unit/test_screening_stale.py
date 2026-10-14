"""Unit: screeners drop symbols whose last bar is older than ``max_stale_days`` before as-of."""

from __future__ import annotations

import datetime as dt

import pytest

from honba_examples.screening import graham, momentum, value
from honba_examples.screening.dispatch import run_screener
from tests.unit.conftest import SCREEN_ASOF

STALE_LAST = SCREEN_ASOF - dt.timedelta(days=40)


@pytest.fixture
def no_fund(monkeypatch):
    for mod in (momentum, value):
        monkeypatch.setattr(mod, "find_duckdb_path", lambda: None)


def _uptrend(n=300):
    return [100.0 * 1.001**i for i in range(n)]


def test_multibagger_drops_stale_symbol(catalog_dir, add_series, no_fund) -> None:
    add_series("LIVE", _uptrend())
    add_series("DEAD", _uptrend(), last_date=STALE_LAST)
    syms = lambda **kw: {
        r["symbol"]
        for r in momentum.screen_multibaggers(
            catalog_dir, SCREEN_ASOF, train_years=2, min_return_1y=5.0, **kw
        )
    }
    assert syms() == {"LIVE"}  # default max_stale_days=10
    assert syms(max_stale_days=None) == {"LIVE", "DEAD"}
    assert syms(max_stale_days=60) == {"LIVE", "DEAD"}


def test_value_drops_stale_symbol(catalog_dir, add_series, no_fund) -> None:
    flat = [100.0] * 300
    add_series("LIVE", flat)
    add_series("DEAD", flat, last_date=STALE_LAST)

    def syms(**kw):
        rows = value.screen_value_and_dividend(
            catalog_dir, SCREEN_ASOF, train_years=2, max_pct_from_52w_low=50.0, **kw
        )
        return {r["symbol"] for r in rows}

    assert syms() == {"LIVE"}
    assert syms(max_stale_days=None) == {"LIVE", "DEAD"}


def test_net_net_drops_stale_symbol(catalog_dir, add_series, fund, monkeypatch) -> None:
    monkeypatch.setattr(graham, "find_duckdb_path", lambda: fund.path)
    for sym, last in (("LIVE", SCREEN_ASOF), ("DEAD", STALE_LAST)):
        add_series(sym, [40.0] * 150, last_date=last)
        fund.stmt(
            sym,
            dt.date(2024, 3, 31),
            {
                "Current Assets": 1000.0,
                "Total Liabilities Net Minority Interest": 200.0,
                "Diluted Average Shares": 10.0,
            },
        )
    got = lambda **kw: {r["symbol"] for r in graham.screen_net_net(catalog_dir, SCREEN_ASOF, **kw)}
    assert got() == {"LIVE"}
    assert got(max_stale_days=None) == {"LIVE", "DEAD"}
    assert got(max_stale_days=0) == {"LIVE", "DEAD"}  # 0 disables, like None


def test_run_screener_passes_max_stale_days(catalog_dir, add_series, no_fund) -> None:
    add_series("LIVE", _uptrend())
    add_series("DEAD", _uptrend(), last_date=STALE_LAST)
    args = ("multibagger", catalog_dir, SCREEN_ASOF, 2, 10)
    assert {r["symbol"] for r in run_screener(*args)} == {"LIVE"}
    assert {r["symbol"] for r in run_screener(*args, max_stale_days=0)} == {"LIVE", "DEAD"}


def test_pipeline_screeners_expose_and_pass_max_stale_days(
    catalog_dir, add_series, no_fund
) -> None:
    from honba_examples.pipeline import DividendYieldScreener, MultibaggerScreener, ValueScreener

    for cls in (MultibaggerScreener, ValueScreener, DividendYieldScreener):
        spec = cls.describe()["params"]["max_stale_days"]
        assert (spec["default"], spec["min"]) == (10, 0) and spec["max"] >= 365
        assert cls().max_stale_days == 10
    add_series("RELIANCE", _uptrend())  # anchors get_latest_market_date to SCREEN_ASOF
    add_series("DEAD", _uptrend(), last_date=STALE_LAST)
    kw = {"asof": SCREEN_ASOF, "catalog_dir": catalog_dir, "train_years": 2, "min_return_1y": 5.0}
    strict = MultibaggerScreener(**kw).run({})
    loose = MultibaggerScreener(max_stale_days=0, **kw).run({})
    assert strict["symbols"] == ["RELIANCE"]
    assert set(loose["symbols"]) == {"RELIANCE", "DEAD"}
