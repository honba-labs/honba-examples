"""Unit: ``report=[...]`` appends requested columns to candidates without filtering on them."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import pytest

from honba_examples.pipeline import (
    DividendYieldScreener,
    FundamentalQualityFilter,
    ValueScreener,
)
from honba_examples.pipeline.reporting import REPORTABLE, add_report_columns

ASOF = dt.date(2024, 6, 30)


@pytest.fixture
def fund_db(tmp_path: Path) -> Path:
    path = tmp_path / "fundamentals.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        """CREATE TABLE financial_ratios (symbol VARCHAR, period_type VARCHAR, fiscal_date DATE,
        roce_pct DOUBLE, roe_pct DOUBLE, operating_margin_pct DOUBLE, net_margin_pct DOUBLE,
        debt_to_equity DOUBLE, eps DOUBLE, free_cash_flow DOUBLE, stockholders_equity DOUBLE)"""
    )
    con.execute(
        "CREATE TABLE financial_statements (symbol VARCHAR, period_type VARCHAR, "
        "fiscal_date DATE, line_item VARCHAR, value DOUBLE)"
    )
    con.execute(
        """INSERT INTO financial_ratios VALUES
        ('AAA', 'FY', '2024-03-31', 18.04, 12.0, 20.0, 10.0, 0.456, 10.0, 5e8, 1000.0),
        ('AAA', 'FY', '2025-03-31', 99.0, 99.0, 99.0, 99.0, 9.0, 99.0, 9e9, 9e9),
        ('NEG', 'FY', '2024-03-31', 5.0, 2.0, 3.0, 1.0, 2.0, -4.0, -1.0, 500.0)"""
    )
    con.execute(
        """INSERT INTO financial_statements VALUES
        ('AAA', 'FY', '2024-03-31', 'Diluted Average Shares', 100.0),
        ('AAA', 'FY', '2024-03-31', 'Cash Dividends Paid', -300.0),
        ('NEG', 'FY', '2024-03-31', 'Diluted Average Shares', 50.0)"""
    )
    con.close()
    return path


def _cands() -> list[dict]:
    return [
        {"symbol": "AAA", "close": 200.0},
        {"symbol": "NEG", "close": 40.0},
        {"symbol": "NODATA", "close": 10.0},
    ]


def test_columns_are_derived_point_in_time(fund_db: Path) -> None:
    cols = ["market_cap", "dividend_yield_pct", "pe_ratio", "pb_ratio", "roce_pct"]
    rows = {r["symbol"]: r for r in add_report_columns(_cands(), cols, ASOF, db_path=fund_db)}
    aaa = rows["AAA"]
    assert aaa["market_cap"] == 200.0 * 100.0  # close x diluted shares
    assert aaa["dividend_yield_pct"] == 1.5  # 300 / 100 shares / 200 close
    assert aaa["pe_ratio"] == 20.0  # 200 / 10
    assert aaa["pb_ratio"] == 20.0  # 200 / (1000 / 100)
    assert aaa["roce_pct"] == 18.0  # FY2025 row is after the as-of date and ignored
    neg = rows["NEG"]
    assert neg["pe_ratio"] is None  # negative EPS: no P/E
    assert neg["dividend_yield_pct"] == 0.0
    assert neg["market_cap"] == 2000.0
    assert all(rows["NODATA"][c] is None for c in cols)  # unknown symbol: None, not invented


def test_existing_columns_and_inputs_are_left_alone(fund_db: Path) -> None:
    cands = [{"symbol": "AAA", "close": 200.0, "roce_pct": 1.0}]
    out = add_report_columns(cands, ["roce_pct", "eps"], ASOF, db_path=fund_db)
    assert out[0]["roce_pct"] == 1.0 and out[0]["eps"] == 10.0
    assert "eps" not in cands[0]
    assert add_report_columns(cands, [], ASOF, db_path=fund_db) is cands


def test_unreportable_column_lists_the_reportable_ones(fund_db: Path) -> None:
    with pytest.raises(ValueError, match="ev_ebitda") as exc:
        add_report_columns(_cands(), ["market_cap", "ev_ebitda"], ASOF, db_path=fund_db)
    assert all(c in str(exc.value) for c in REPORTABLE)
    with pytest.raises(ValueError, match="ev_ebitda"):
        FundamentalQualityFilter(report=["ev_ebitda"])  # fails at construction, not mid-run
    with pytest.raises(ValueError, match="ev_ebitda"):
        ValueScreener(report=["ev_ebitda"])


def test_missing_database_or_close_is_an_error(
    fund_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("honba_examples.pipeline.reporting.find_duckdb_path", lambda: None)
    with pytest.raises(ValueError, match=r"fundamentals\.duckdb not found"):
        add_report_columns(_cands(), ["market_cap"], ASOF)
    with pytest.raises(ValueError, match="no 'close'"):
        add_report_columns([{"symbol": "AAA"}], ["market_cap"], ASOF, db_path=fund_db)
    with pytest.raises(ValueError, match="as-of date"):
        add_report_columns(_cands(), ["market_cap"], None, db_path=fund_db)


def test_filter_reports_without_filtering_on_the_column(
    fund_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("honba_examples.pipeline.reporting.find_duckdb_path", lambda: fund_db)
    ctx = {
        "candidates": [
            {"symbol": "AAA", "close": 200.0, "roce_pct": 18.0, "debt_to_equity": 0.4},
            {"symbol": "NEG", "close": 40.0, "roce_pct": 5.0, "debt_to_equity": 2.0},
        ],
        "symbols": ["AAA", "NEG"],
        "asof": ASOF,
    }
    out = FundamentalQualityFilter(min_roce=15.0, report=["market_cap"]).run(ctx)
    assert out["symbols"] == ["AAA"]
    assert out["candidates"] == [
        {
            "symbol": "AAA",
            "close": 200.0,
            "roce_pct": 18.0,
            "debt_to_equity": 0.4,
            "market_cap": 20000.0,
        }
    ]


def test_screener_report_on_real_data_matches_its_own_columns() -> None:
    plain = ValueScreener(asof="2025-03-01", max_pe=20.0, max_pb=2.0, top=3).run({})
    rich = ValueScreener(
        asof="2025-03-01",
        max_pe=20.0,
        max_pb=2.0,
        top=3,
        report=["market_cap", "dividend_yield_pct", "pe_ratio", "roce_pct"],
    ).run({})
    assert rich["symbols"] == plain["symbols"] and rich["symbols"]
    for a, b in zip(plain["candidates"], rich["candidates"], strict=True):
        assert b["pe_ratio"] == a["pe_ratio"] and b["roce_pct"] == a["roce_pct"]
        assert b["dividend_yield_pct"] == a["div_yield_pct"]
        assert "market_cap" in b and "market_cap" not in a
        assert b["market_cap"] is None or b["market_cap"] > 0
    div = DividendYieldScreener(asof="2025-03-01", top=3, report=["market_cap"]).run({})
    assert all("market_cap" in c for c in div["candidates"])
