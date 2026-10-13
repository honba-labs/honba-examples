"""honba_examples.pipeline.screeners: point-in-time screeners and shared price readers.

Each screener is a thin :class:`~honba_examples.pipeline.base.Step` wrapper over the generic
engine in :mod:`honba_examples.screening`, producing ``{"candidates": [...], "symbols": [...]}``
on the pipeline context. This module also hosts the lightweight DuckDB price readers reused by
the filters and sizers (benchmark SMA, average turnover, realized volatility).
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Any

from honba_examples.pipeline.base import Step, as_date
from honba_examples.screening import (
    find_catalog_path,
    get_latest_market_date,
    screen_multibaggers,
    screen_value_and_dividend,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DividendYieldScreener",
    "MultibaggerScreener",
    "ScreenerStep",
    "ValueScreener",
    "average_daily_turnover",
    "latest_close_and_sma",
    "realized_volatility",
    "symbol_parquet_files",
]


def symbol_parquet_files(catalog_dir: Path, symbol: str) -> list[str]:
    """Return the parquet file paths for ``symbol`` (empty when the symbol is absent)."""
    sym_dir = catalog_dir / symbol
    if not sym_dir.is_dir():
        return []
    return [str(f) for f in sorted(sym_dir.glob("*.parquet"))]


def _asof_clause(asof: dt.date | None) -> str:
    if asof is None:
        return ""
    return f"WHERE epoch_ms(ts // 1000000)::DATE <= DATE '{asof.isoformat()}'"


class ScreenerStep(Step):
    """Base class for screeners wrapping the point-in-time screening engine."""

    name = "screener"

    def __init__(
        self,
        asof: str | dt.date | None = None,
        train_years: int = 3,
        top: int | None = None,
        catalog_dir: Path | str | None = None,
    ) -> None:
        self.asof = asof
        self.train_years = train_years
        self.top = top
        self.catalog_dir = Path(catalog_dir) if catalog_dir is not None else None

    def resolve_catalog_dir(self) -> Path:
        if self.catalog_dir is not None:
            return find_catalog_path(self.catalog_dir)
        return find_catalog_path()

    def resolve_asof(self, catalog_dir: Path) -> dt.date:
        latest = get_latest_market_date(catalog_dir)
        asof = as_date(self.asof)
        if asof is None:
            return latest
        if asof > latest:
            logger.warning(
                "%s: asof %s is beyond the latest catalog date %s; using %s",
                self.name,
                asof,
                latest,
                latest,
            )
            return latest
        return asof

    def screen(self, catalog_dir: Path, asof: dt.date) -> list[dict[str, Any]]:
        raise NotImplementedError("Screeners must implement screen()")

    def run(self, ctx: dict[str, Any]) -> dict[str, Any]:
        catalog_dir = self.resolve_catalog_dir()
        asof = self.resolve_asof(catalog_dir)
        candidates = self.screen(catalog_dir, asof)
        ctx["candidates"] = candidates
        ctx["symbols"] = [c["symbol"] for c in candidates]
        ctx["asof"] = asof
        ctx["catalog_dir"] = catalog_dir
        logger.info("%s: %d candidates as of %s", self.name, len(candidates), asof)
        return ctx


class MultibaggerScreener(ScreenerStep):
    """Stage 2 prospective multibagger screener (momentum + capital efficiency)."""

    name = "multibagger_screener"

    def __init__(
        self,
        asof: str | dt.date | None = None,
        train_years: int = 3,
        min_return_1y: float = 15.0,
        max_return_1y: float | None = None,
        max_pct_from_high: float = 15.0,
        max_debt_to_equity: float = 1.5,
        top: int | None = None,
        catalog_dir: Path | str | None = None,
    ) -> None:
        super().__init__(asof=asof, train_years=train_years, top=top, catalog_dir=catalog_dir)
        self.min_return_1y = min_return_1y
        self.max_return_1y = max_return_1y
        self.max_pct_from_high = max_pct_from_high
        self.max_debt_to_equity = max_debt_to_equity

    def screen(self, catalog_dir: Path, asof: dt.date) -> list[dict[str, Any]]:
        return screen_multibaggers(
            catalog_dir=catalog_dir,
            asof_date=asof,
            train_years=self.train_years,
            min_return_1y=self.min_return_1y,
            max_return_1y=self.max_return_1y,
            max_pct_from_high=self.max_pct_from_high,
            max_debt_to_equity=self.max_debt_to_equity,
            top=self.top,
        )


class ValueScreener(ScreenerStep):
    """Deep value screener: low P/E, low P/B, trading near the 52-week low."""

    name = "value_screener"

    def __init__(
        self,
        asof: str | dt.date | None = None,
        train_years: int = 3,
        max_pct_from_52w_low: float = 10.0,
        min_roce: float = 10.0,
        max_pe: float | None = 20.0,
        max_pb: float | None = 2.0,
        max_debt_equity: float = 1.5,
        require_positive_fcf: bool = False,
        top: int | None = None,
        catalog_dir: Path | str | None = None,
    ) -> None:
        super().__init__(asof=asof, train_years=train_years, top=top, catalog_dir=catalog_dir)
        self.max_pct_from_52w_low = max_pct_from_52w_low
        self.min_roce = min_roce
        self.max_pe = max_pe
        self.max_pb = max_pb
        self.max_debt_equity = max_debt_equity
        self.require_positive_fcf = require_positive_fcf

    def screen(self, catalog_dir: Path, asof: dt.date) -> list[dict[str, Any]]:
        return screen_value_and_dividend(
            catalog_dir=catalog_dir,
            asof_date=asof,
            train_years=self.train_years,
            mode="value",
            max_pct_from_52w_low=self.max_pct_from_52w_low,
            min_roce=self.min_roce,
            max_pe=self.max_pe,
            max_pb=self.max_pb,
            max_debt_equity=self.max_debt_equity,
            require_positive_fcf=self.require_positive_fcf,
            top=self.top,
        )


class DividendYieldScreener(ScreenerStep):
    """High dividend yielders near the 52-week low with positive free cash flow."""

    name = "dividend_yield_screener"

    def __init__(
        self,
        asof: str | dt.date | None = None,
        train_years: int = 3,
        min_yield_pct: float = 3.0,
        max_pct_from_52w_low: float = 10.0,
        min_roce: float = 8.0,
        max_debt_equity: float = 1.5,
        require_positive_fcf: bool = True,
        top: int | None = None,
        catalog_dir: Path | str | None = None,
    ) -> None:
        super().__init__(asof=asof, train_years=train_years, top=top, catalog_dir=catalog_dir)
        self.min_yield_pct = min_yield_pct
        self.max_pct_from_52w_low = max_pct_from_52w_low
        self.min_roce = min_roce
        self.max_debt_equity = max_debt_equity
        self.require_positive_fcf = require_positive_fcf

    def screen(self, catalog_dir: Path, asof: dt.date) -> list[dict[str, Any]]:
        return screen_value_and_dividend(
            catalog_dir=catalog_dir,
            asof_date=asof,
            train_years=self.train_years,
            mode="dividend",
            min_div_yield=self.min_yield_pct,
            max_pct_from_52w_low=self.max_pct_from_52w_low,
            min_roce=self.min_roce,
            max_debt_equity=self.max_debt_equity,
            require_positive_fcf=self.require_positive_fcf,
            top=self.top,
        )


def latest_close_and_sma(
    catalog_dir: Path,
    symbol: str,
    asof: dt.date | None,
    window: int = 200,
) -> tuple[float, float, int] | None:
    """Return ``(close, sma, bar_count)`` for the latest bar on/before ``asof``, else ``None``."""
    import duckdb

    files = symbol_parquet_files(catalog_dir, symbol)
    if not files:
        return None
    query = f"""
    WITH bars AS (
        SELECT epoch_ms(ts // 1000000)::DATE AS d, close
        FROM read_parquet({files})
        {_asof_clause(asof)}
    ),
    ranked AS (
        SELECT close,
               AVG(close) OVER (ORDER BY d ROWS BETWEEN {window - 1} PRECEDING AND CURRENT ROW)
                   AS sma,
               ROW_NUMBER() OVER (ORDER BY d DESC) AS rn,
               COUNT(*) OVER () AS n
        FROM bars
    )
    SELECT close, sma, n FROM ranked WHERE rn = 1
    """
    row = duckdb.connect().execute(query).fetchone()
    if row is None or row[0] is None:
        return None
    return float(row[0]), float(row[1]), int(row[2])


def average_daily_turnover(
    catalog_dir: Path,
    symbols: list[str],
    asof: dt.date | None,
    lookback_days: int = 30,
) -> dict[str, float]:
    """Average daily traded value (``close * volume``) over the last ``lookback_days`` sessions."""
    import duckdb

    files: list[str] = []
    for symbol in symbols:
        files.extend(symbol_parquet_files(catalog_dir, symbol))
    if not files:
        return {}
    query = f"""
    WITH bars AS (
        SELECT regexp_extract(filename, 'NSE/([^/]+)/', 1) AS sym,
               epoch_ms(ts // 1000000)::DATE AS d,
               close * volume AS turnover
        FROM read_parquet({files}, filename=true)
        {_asof_clause(asof)}
    ),
    ranked AS (
        SELECT sym, turnover,
               ROW_NUMBER() OVER (PARTITION BY sym ORDER BY d DESC) AS rn
        FROM bars
    )
    SELECT sym, AVG(turnover) FROM ranked WHERE rn <= {lookback_days} GROUP BY sym
    """
    return {row[0]: float(row[1]) for row in duckdb.connect().execute(query).fetchall()}


def realized_volatility(
    catalog_dir: Path,
    symbols: list[str],
    asof: dt.date | None,
    window: int = 30,
) -> dict[str, float]:
    """Sample standard deviation of the last ``window`` daily simple returns per symbol."""
    import duckdb

    files: list[str] = []
    for symbol in symbols:
        files.extend(symbol_parquet_files(catalog_dir, symbol))
    if not files:
        return {}
    query = f"""
    WITH bars AS (
        SELECT regexp_extract(filename, 'NSE/([^/]+)/', 1) AS sym,
               epoch_ms(ts // 1000000)::DATE AS d,
               close
        FROM read_parquet({files}, filename=true)
        {_asof_clause(asof)}
    ),
    seq AS (
        SELECT sym, close, LAG(close) OVER (PARTITION BY sym ORDER BY d) AS prev,
               ROW_NUMBER() OVER (PARTITION BY sym ORDER BY d DESC) AS rn
        FROM bars
    ),
    rets AS (
        SELECT sym, close / prev - 1.0 AS ret, rn
        FROM seq
        WHERE prev IS NOT NULL AND prev > 0 AND rn <= {window}
    )
    SELECT sym, stddev_samp(ret) FROM rets GROUP BY sym
    """
    result: dict[str, float] = {}
    for row in duckdb.connect().execute(query).fetchall():
        if row[1] is not None:
            result[row[0]] = float(row[1])
    return result
