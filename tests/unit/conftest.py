"""Shared synthetic fixtures for screener tests: a tiny NSE parquet catalog and fundamentals DB."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from pathlib import Path

import duckdb
import pytest

Bar = tuple[dt.date, float, float, float, float, float]  # (date, open, high, low, close, volume)

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
    for d, o, high, low, close, volume in bars:
        ts = int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp()) * 10**9
        con.execute("INSERT INTO b VALUES (?, ?, ?, ?, ?, ?)", [ts, o, high, low, close, volume])
    con.execute(f"COPY b TO '{sym_dir / 'bars.parquet'}' (FORMAT PARQUET)")
    con.close()


@pytest.fixture
def catalog_dir(tmp_path: Path) -> Path:
    path = tmp_path / "catalog" / "1D" / "NSE"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def add_series(catalog_dir: Path) -> Callable[..., None]:
    """``add_series(symbol, closes, last_date=SCREEN_ASOF, lows=None, opens=None, volumes=None)``:
    one bar per session. ``opens`` defaults to ``close`` and ``volumes`` to ``1e6`` for every bar
    (the original behaviour), so existing callers are unaffected.
    """

    def _add(
        symbol: str,
        closes: Sequence[float],
        last_date: dt.date = SCREEN_ASOF,
        lows: Sequence[float] | None = None,
        opens: Sequence[float] | None = None,
        volumes: Sequence[float] | None = None,
    ) -> None:
        days = sessions(last_date, len(closes))
        lows = lows if lows is not None else [c * 0.99 for c in closes]
        opens = opens if opens is not None else list(closes)
        volumes = volumes if volumes is not None else [1e6] * len(closes)
        bars = [
            (d, o, c * 1.01, lo, c, v)
            for d, o, c, lo, v in zip(days, opens, closes, lows, volumes, strict=True)
        ]
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


class FundWriter:
    """Insert ratio / statement rows into the synthetic fundamentals DB."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _exec(self, sql: str, params: list) -> None:
        con = duckdb.connect(str(self.path))
        try:
            con.execute(sql, params)
        finally:
            con.close()

    def ratio(self, symbol: str, fiscal_date: dt.date, period_type: str = "FY", **cols) -> None:
        names = ["symbol", "period_type", "fiscal_date", *cols]
        marks = ", ".join("?" for _ in names)
        self._exec(
            f"INSERT INTO financial_ratios ({', '.join(names)}) VALUES ({marks})",
            [symbol, period_type, fiscal_date, *cols.values()],
        )

    def stmt(self, symbol: str, fiscal_date: dt.date, items: dict[str, float]) -> None:
        for line_item, value in items.items():
            self._exec(
                "INSERT INTO financial_statements VALUES (?, 'FY', 'x', ?, ?, ?, 'INR')",
                [symbol, fiscal_date, line_item, value],
            )

    def company(self, symbol: str, name: str | None = None) -> None:
        self._exec("INSERT INTO companies VALUES (?, ?)", [symbol, name or symbol])


@pytest.fixture
def fund(fund_db: Path) -> FundWriter:
    return FundWriter(fund_db)
