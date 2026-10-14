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
