"""Unit: Graham screeners (net-net default threshold) on synthetic parquet + fundamentals."""

from __future__ import annotations

import datetime as dt

import pytest

from honba_examples.screening import graham
from tests.unit.conftest import SCREEN_ASOF

FY = dt.date(2024, 3, 31)


@pytest.fixture
def netnet_world(catalog_dir, add_series, fund, monkeypatch):
    monkeypatch.setattr(graham, "find_duckdb_path", lambda: fund.path)
    # NCAV/share = (1000 - 200) / 10 = 80; prices 40 (P/NCAV 0.5) and 72 (0.9) and 200 (2.5)
    for sym, price in (("DEEP", 40.0), ("FAIR", 72.0), ("RICH", 200.0)):
        add_series(sym, [price] * 150)
        fund.company(sym)
        fund.ratio(sym, FY, debt_to_equity=0.3)
        fund.stmt(
            sym,
            FY,
            {
                "Current Assets": 1000.0,
                "Total Liabilities Net Minority Interest": 200.0,
                "Current Liabilities": 100.0,
                "Diluted Average Shares": 10.0,
            },
        )
    return catalog_dir


def test_net_net_defaults_to_graham_two_thirds_rule(netnet_world) -> None:
    rows = graham.screen_net_net(netnet_world, SCREEN_ASOF)
    assert [r["symbol"] for r in rows] == ["DEEP"]
    assert rows[0]["price_to_ncav"] == pytest.approx(0.5, abs=0.01)


def test_net_net_threshold_can_be_relaxed_or_disabled(netnet_world) -> None:
    one = graham.screen_net_net(netnet_world, SCREEN_ASOF, max_price_to_ncav=1.0)
    assert {r["symbol"] for r in one} == {"DEEP", "FAIR"}
    ranked = graham.screen_net_net(netnet_world, SCREEN_ASOF, max_price_to_ncav=None)
    assert [r["symbol"] for r in ranked] == ["DEEP", "FAIR", "RICH"]
    assert [
        r["symbol"] for r in graham.screen_net_net(netnet_world, SCREEN_ASOF, max_price_to_ncav=0)
    ] == [
        "DEEP",
        "FAIR",
        "RICH",
    ]


# ---- defensive-mode thresholds: explicit flags override the mode defaults ------------------
@pytest.fixture
def defensive_world(catalog_dir, add_series, fund, monkeypatch):
    monkeypatch.setattr(graham, "find_duckdb_path", lambda: fund.path)
    items = {
        "Current Assets": 200.0,
        "Current Liabilities": 100.0,
        "Diluted Average Shares": 100.0,
        "Cash Dividends Paid": -10.0,
    }
    # price 18, EPS 2, BVPS 10: P/E 9, P/B 1.8, product 16.2, Graham value 21.2 (MoS ~15%)
    for sym, de in (("PB18", 0.3), ("DE08", 0.8)):
        price = 18.0 if sym == "PB18" else 12.0  # DE08: P/E 6, P/B 1.2
        add_series(sym, [price] * 150)
        fund.ratio(sym, FY, eps=2.0, stockholders_equity=1000.0, debt_to_equity=de)
        fund.stmt(sym, FY, items)
    return catalog_dir


def _syms(world, **kw):
    return {r["symbol"] for r in graham.screen_intelligent_investor(world, SCREEN_ASOF, **kw)}


def test_defensive_defaults_apply_when_flags_not_given(defensive_world) -> None:
    assert _syms(defensive_world) == set()  # PB18 fails P/B 1.5, DE08 fails D/E 0.6


def test_explicit_flags_override_defensive_defaults(defensive_world) -> None:
    assert _syms(defensive_world, max_pb=2.0) == {"PB18"}
    assert _syms(defensive_world, max_debt_equity=1.0) == {"DE08"}
    assert _syms(defensive_world, max_pb=0, max_debt_equity=0) == {"PB18", "DE08"}  # 0 disables
    assert _syms(defensive_world, max_pb=1.0, max_debt_equity=1.0) == set()  # tighter still wins


def test_effective_thresholds_reported() -> None:
    d = graham.graham_thresholds("defensive")
    assert (d["max_pe"], d["max_pb"], d["max_graham_product"], d["max_debt_equity"]) == (
        15.0,
        1.5,
        22.5,
        0.6,
    )
    e = graham.graham_thresholds("enterprising")
    assert (e["max_pe"], e["max_pb"], e["max_debt_equity"]) == (20.0, 2.0, 1.0)
    o = graham.graham_thresholds("defensive", max_pe=25.0, max_pb=0)
    assert o["max_pe"] == 25.0 and o["max_pb"] is None and o["max_debt_equity"] == 0.6
