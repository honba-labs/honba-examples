"""Shared synthetic fixtures for screener tests: a tiny NSE parquet catalog and fundamentals DB."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from pathlib import Path

import duckdb
import pytest

Bar = tuple[dt.date, float, float, float]  # (date, high, low, close)

SCREEN_ASOF = dt.date(2024, 6, 28)


def sessions(end: dt.date, n: int) -> list[dt.date]:
    """The last ``n`` weekdays ending on ``end`` (inclusive), oldest first."""
    days: list[dt.date] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= dt.timedelta(days=1)
    return days[::-1]


def write_symbol(catalog: Path, symbol: str, bars: Sequence[Bar]) -> None:
    """Write ``bars`` as ``<catalog>/<symbol>/bars.parquet`` (ts in epoch nanoseconds)."""
    sym_dir = catalog / symbol
    sym_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE b (ts BIGINT, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, "
        "volume DOUBLE)"
    )
    for d, high, low, close in bars:
        ts = int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp()) * 10**9
        con.execute("INSERT INTO b VALUES (?, ?, ?, ?, ?, ?)", [ts, close, high, low, close, 1e6])
    con.execute(f"COPY b TO '{sym_dir / 'bars.parquet'}' (FORMAT PARQUET)")
    con.close()


@pytest.fixture
def catalog_dir(tmp_path: Path) -> Path:
    path = tmp_path / "catalog" / "1D" / "NSE"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def add_series(catalog_dir: Path) -> Callable[..., None]:
    """``add_series(symbol, closes, last_date=SCREEN_ASOF, lows=None)``: one bar per session."""

    def _add(
        symbol: str,
        closes: Sequence[float],
        last_date: dt.date = SCREEN_ASOF,
        lows: Sequence[float] | None = None,
    ) -> None:
        days = sessions(last_date, len(closes))
        lows = lows if lows is not None else [c * 0.99 for c in closes]
        bars = [(d, c * 1.01, lo, c) for d, c, lo in zip(days, closes, lows, strict=True)]
        write_symbol(catalog_dir, symbol, bars)

    return _add


@pytest.fixture
def fund_db(tmp_path: Path) -> Path:
    """Empty fundamentals DB with the real table shapes; tests insert rows via ``con``."""
    path = tmp_path / "fundamentals.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE companies (symbol VARCHAR, company_name VARCHAR)")
    con.execute(
        """CREATE TABLE financial_ratios (symbol VARCHAR, period_type VARCHAR, fiscal_date DATE,
        revenue DOUBLE, net_income DOUBLE, stockholders_equity DOUBLE, total_debt DOUBLE,
        operating_cash_flow DOUBLE, free_cash_flow DOUBLE, roce_pct DOUBLE, roe_pct DOUBLE,
        operating_margin_pct DOUBLE, net_margin_pct DOUBLE, debt_to_equity DOUBLE, eps DOUBLE)"""
    )
    con.execute(
        "CREATE TABLE financial_statements (symbol VARCHAR, period_type VARCHAR, "
        "statement_type VARCHAR, fiscal_date DATE, line_item VARCHAR, value DOUBLE, "
        "currency VARCHAR)"
    )
    con.close()
    return path
