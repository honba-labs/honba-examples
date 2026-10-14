"""honba_examples.pipeline.screeners: point-in-time screeners and shared price readers.

Each screener is a thin :class:`~honba_examples.pipeline.base.Step` wrapper over the generic
engine in :mod:`honba_examples.screening`, producing ``{"candidates": [...], "symbols": [...]}``
on the pipeline context. This module also hosts the lightweight DuckDB price readers reused by
the filters and sizers (benchmark SMA, average turnover, realized volatility).
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from honba_examples.pipeline.base import Step, as_date
from honba_examples.pipeline.manifest import manifest, param
from honba_examples.pipeline.reporting import (
    add_report_columns,
    check_report_columns,
    reportable_beyond,
)
from honba_examples.screening import (
    find_catalog_path,
    get_latest_market_date,
    screen_multibaggers,
    screen_value_and_dividend,
)
from honba_examples.screening.common import DEFAULT_MAX_STALE_DAYS

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


_MULTIBAGGER_COLUMNS = [
    "company_name",
    "asof_date",
    "close",
    "ret_1y",
    "ret_3m",
    "train_ret_pct",
    "pct_from_high",
    "sma200",
    "sma200_rising",
    "vol_surge",
    "roce_pct",
    "debt_to_equity",
    "mb_score",
]
_VALUE_COLUMNS = [
    "company_name",
    "asof_date",
    "close",
    "low52",
    "pct_from_low",
    "train_ret_pct",
    "hit_52w_low_recently",
    "div_yield_pct",
    "pe_ratio",
    "pb_ratio",
    "roce_pct",
    "debt_to_equity",
    "operating_margin_pct",
    "free_cash_flow",
]


def _screen_params(**extra: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Params shared by every screener (``extra`` first, as they read best in a form)."""
    return {
        **extra,
        "asof": param(
            "date",
            "Evaluation date (YYYY-MM-DD); defaults to the latest catalog session.",
            optional=True,
        ),
        "train_years": param(
            "int", "In-sample price history window in years.", default=3, min=1, max=20, step=1
        ),
        "max_stale_days": param(
            "int",
            "Skip symbols whose last bar is more than this many calendar days before the "
            "evaluation date (delisted/suspended); 0 disables.",
            default=DEFAULT_MAX_STALE_DAYS,
            min=0,
            max=3650,
            step=1,
        ),
        "allow_missing": param(
            "bool",
            "Let symbols with missing fundamentals (no row, or NULL debt/equity or ROCE) pass "
            "those gates instead of failing them.",
            default=False,
        ),
        "top": param("int", "Keep only the best N candidates.", optional=True, min=1, step=1),
    }


class ScreenerStep(Step):
    """Base class for screeners wrapping the point-in-time screening engine."""

    name = "screener"

    def __init__(
        self,
        asof: str | dt.date | None = None,
        train_years: int = 3,
        top: int | None = None,
        catalog_dir: Path | str | None = None,
        report: Sequence[str] | None = None,
        max_stale_days: int | None = DEFAULT_MAX_STALE_DAYS,
        allow_missing: bool = False,
    ) -> None:
        self.max_stale_days = max_stale_days
        self.allow_missing = allow_missing
        self.asof = asof
        self.train_years = train_years
        self.top = top
        self.report = check_report_columns(report or [])
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
        if self.report:
            candidates = add_report_columns(candidates, self.report, asof)
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
        report: Sequence[str] | None = None,
        max_stale_days: int | None = DEFAULT_MAX_STALE_DAYS,
        allow_missing: bool = False,
    ) -> None:
        super().__init__(
            asof=asof,
            train_years=train_years,
            top=top,
            catalog_dir=catalog_dir,
            report=report,
            max_stale_days=max_stale_days,
            allow_missing=allow_missing,
        )
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
            max_stale_days=self.max_stale_days,
            allow_missing=self.allow_missing,
            top=self.top,
        )

    @classmethod
    def describe(cls) -> dict[str, Any]:
        return manifest(
            "screen.multibagger",
            "Prospective multibagger (Stage 2)",
            "Minervini-style trend template with a tight base, positive 3-month momentum, a "
            "volume surge and a leverage cap; ranked by a momentum/capital-efficiency score.",
            _screen_params(
                min_return_1y=param(
                    "float", "Minimum 1-year return (%).", default=15.0, min=-100.0, max=1000.0
                ),
                max_return_1y=param(
                    "float",
                    "Maximum 1-year return (%), to skip exhausted runs.",
                    optional=True,
                    min=-100.0,
                    max=10000.0,
                ),
                max_pct_from_high=param(
                    "float",
                    "Maximum distance below the 52-week high (%).",
                    default=15.0,
                    min=0.0,
                    max=100.0,
                    step=0.5,
                ),
                max_debt_to_equity=param(
                    "float",
                    "Maximum debt-to-equity (a missing value fails unless allow_missing).",
                    default=1.5,
                    min=0.0,
                    max=20.0,
                    step=0.1,
                ),
            ),
            "universe",
            "candidates",
            requires={
                "bars": ["close", "high", "volume", "ts"],
                "fundamentals": ["debt_to_equity"],
                "indicators": [
                    {"kind": "sma", "period": 50, "as": "sma50"},
                    {"kind": "sma", "period": 200, "as": "sma200"},
                ],
                "history_bars": 252,
                "pit": True,
            },
            reportable=reportable_beyond(_MULTIBAGGER_COLUMNS),
            adds=_MULTIBAGGER_COLUMNS,
            tags=["momentum", "growth", "point_in_time"],
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
        report: Sequence[str] | None = None,
        max_stale_days: int | None = DEFAULT_MAX_STALE_DAYS,
        allow_missing: bool = False,
    ) -> None:
        super().__init__(
            asof=asof,
            train_years=train_years,
            top=top,
            catalog_dir=catalog_dir,
            report=report,
            max_stale_days=max_stale_days,
            allow_missing=allow_missing,
        )
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
            max_stale_days=self.max_stale_days,
            allow_missing=self.allow_missing,
            top=self.top,
        )

    @classmethod
    def describe(cls) -> dict[str, Any]:
        return manifest(
            "screen.value",
            "Deep value near the 52-week low",
            "Trades within a set distance of its 52-week low with ROCE, leverage and optional "
            "derived P/E, P/B and free-cash-flow tests; ranked by closeness to the low.",
            _screen_params(
                max_pct_from_52w_low=param(
                    "float",
                    "Maximum distance above the 52-week low (%).",
                    default=10.0,
                    min=0.0,
                    max=100.0,
                    step=0.5,
                ),
                min_roce=param("float", "Minimum ROCE (%).", default=10.0, min=-100.0, max=100.0),
                max_pe=param(
                    "float",
                    "Maximum derived P/E; 0 disables the test.",
                    default=20.0,
                    min=0.0,
                    max=500.0,
                    step=0.5,
                ),
                max_pb=param(
                    "float",
                    "Maximum derived P/B; 0 disables the test.",
                    default=2.0,
                    min=0.0,
                    max=100.0,
                    step=0.1,
                ),
                max_debt_equity=param(
                    "float", "Maximum debt-to-equity.", default=1.5, min=0.0, max=20.0, step=0.1
                ),
                require_positive_fcf=param(
                    "bool", "Require positive free cash flow.", default=False
                ),
            ),
            "universe",
            "candidates",
            requires={
                "bars": ["close", "low", "ts"],
                "fundamentals": [
                    "roce_pct",
                    "debt_to_equity",
                    "eps",
                    "stockholders_equity",
                    "free_cash_flow",
                ],
                "history_bars": 252,
                "pit": True,
            },
            adds=_VALUE_COLUMNS,
            reportable=reportable_beyond(_VALUE_COLUMNS),
            tags=["value", "contrarian", "point_in_time"],
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
        report: Sequence[str] | None = None,
        max_stale_days: int | None = DEFAULT_MAX_STALE_DAYS,
        allow_missing: bool = False,
    ) -> None:
        super().__init__(
            asof=asof,
            train_years=train_years,
            top=top,
            catalog_dir=catalog_dir,
            report=report,
            max_stale_days=max_stale_days,
            allow_missing=allow_missing,
        )
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
            max_stale_days=self.max_stale_days,
            allow_missing=self.allow_missing,
            top=self.top,
        )

    @classmethod
    def describe(cls) -> dict[str, Any]:
        return manifest(
            "screen.dividend_yield",
            "High dividend yield near the 52-week low",
            "Trailing cash-dividend yield above a floor while within a set distance of the "
            "52-week low, with ROCE, leverage and free-cash-flow tests; ranked by yield.",
            _screen_params(
                min_yield_pct=param(
                    "float",
                    "Minimum trailing dividend yield (%).",
                    default=3.0,
                    min=0.0,
                    max=100.0,
                    step=0.1,
                ),
                max_pct_from_52w_low=param(
                    "float",
                    "Maximum distance above the 52-week low (%).",
                    default=10.0,
                    min=0.0,
                    max=100.0,
                    step=0.5,
                ),
                min_roce=param("float", "Minimum ROCE (%).", default=8.0, min=-100.0, max=100.0),
                max_debt_equity=param(
                    "float", "Maximum debt-to-equity.", default=1.5, min=0.0, max=20.0, step=0.1
                ),
                require_positive_fcf=param(
                    "bool", "Require positive free cash flow.", default=True
                ),
            ),
            "universe",
            "candidates",
            requires={
                "bars": ["close", "low", "ts"],
                "fundamentals": [
                    "roce_pct",
                    "debt_to_equity",
                    "free_cash_flow",
                    "dividends_paid",
                    "diluted_average_shares",
                ],
                "history_bars": 252,
                "pit": True,
            },
            adds=_VALUE_COLUMNS,
            reportable=reportable_beyond(_VALUE_COLUMNS),
            tags=["dividend", "income", "point_in_time"],
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
