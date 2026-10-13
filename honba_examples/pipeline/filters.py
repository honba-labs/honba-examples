"""honba_examples.pipeline.filters: quality, regime and liquidity filters.

Filters consume the candidate list produced by a screener and narrow it before sizing:
``FundamentalQualityFilter`` (ROCE / debt / margin), ``MarketRegimeFilter`` (benchmark above its
200-day SMA) and ``LiquidityFilter`` (average daily turnover).
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path
from typing import Any

from honba_examples.pipeline.base import Step
from honba_examples.pipeline.screeners import average_daily_turnover, latest_close_and_sma
from honba_examples.screening import find_catalog_path

logger = logging.getLogger(__name__)

__all__ = ["FundamentalQualityFilter", "LiquidityFilter", "MarketRegimeFilter"]

_CONDITION_RE = re.compile(r"^(above|below)_sma(\d+)$")

_BENCHMARK_ALIASES: dict[str, list[str]] = {
    "NIFTY": ["NIFTY", "NIFTY50", "NIFTY_50", "NIFTYBEES", "^NSEI", "NIFTY 50"],
    "NIFTY50": ["NIFTY50", "NIFTY", "NIFTY_50", "NIFTYBEES", "^NSEI"],
    "NIFTY_50": ["NIFTY_50", "NIFTY", "NIFTY50", "NIFTYBEES", "^NSEI", "NIFTY 50"],
    "SENSEX": ["SENSEX", "^BSESN", "SENSEXBEES"],
}


def _filter_candidates(ctx: dict[str, Any], keep: set[str]) -> dict[str, Any]:
    """Restrict ``symbols``/``candidates`` in ``ctx`` to the symbols in ``keep``."""
    ctx["symbols"] = [s for s in ctx.get("symbols", []) if s in keep]
    candidates = ctx.get("candidates")
    if isinstance(candidates, list):
        ctx["candidates"] = [c for c in candidates if c.get("symbol") in keep]
    return ctx


class FundamentalQualityFilter(Step):
    """Filter candidates by return on capital, leverage and operating margin."""

    name = "fundamental_quality"

    def __init__(
        self,
        min_roce: float = 15.0,
        max_debt_to_equity: float = 1.0,
        min_operating_margin: float = 0.0,
    ) -> None:
        self.min_roce = min_roce
        self.max_debt_to_equity = max_debt_to_equity
        self.min_operating_margin = min_operating_margin

    def _passes(self, candidate: dict[str, Any]) -> bool:
        roce = candidate.get("roce_pct")
        if roce is not None and float(roce) < self.min_roce:
            return False
        debt = candidate.get("debt_to_equity")
        if debt is not None and float(debt) > self.max_debt_to_equity:
            return False
        margin = candidate.get("operating_margin_pct")
        return margin is None or float(margin) > self.min_operating_margin

    def run(self, ctx: dict[str, Any]) -> dict[str, Any]:
        candidates = ctx.get("candidates")
        if not isinstance(candidates, list):
            return ctx
        kept = [c for c in candidates if self._passes(c)]
        logger.info("%s: kept %d/%d candidates", self.name, len(kept), len(candidates))
        return _filter_candidates(ctx, {c.get("symbol") for c in kept})


class MarketRegimeFilter(Step):
    """Block new buys unless the benchmark satisfies the requested SMA condition.

    When the benchmark symbol is unavailable or lacks sufficient history the filter passes
    through with a warning rather than failing the pipeline.
    """

    name = "market_regime"

    def __init__(
        self,
        benchmark: str = "NIFTY",
        condition: str = "above_sma200",
        window: int = 200,
        catalog_dir: Path | str | None = None,
    ) -> None:
        self.benchmark = benchmark
        self.condition = condition
        self.window = window
        self.catalog_dir = Path(catalog_dir) if catalog_dir is not None else None
        match = _CONDITION_RE.match(condition)
        self.direction = match.group(1) if match else "above"
        self.window = int(match.group(2)) if match else window

    def resolve_catalog_dir(self, ctx: dict[str, Any]) -> Path:
        if self.catalog_dir is not None:
            return find_catalog_path(self.catalog_dir)
        existing = ctx.get("catalog_dir")
        if isinstance(existing, Path):
            return existing
        return find_catalog_path()

    def _resolve_benchmark_symbol(self, catalog_dir: Path) -> str | None:
        upper = self.benchmark.upper()
        normalized = upper.replace(" ", "_").replace("-", "_")
        alias_keys = [normalized, upper]
        raw_names: list[str] = [self.benchmark]
        for key in alias_keys:
            aliases = _BENCHMARK_ALIASES.get(key, [])
            for name in aliases:
                if name not in raw_names:
                    raw_names.append(name)
        # Also try a filesystem-friendly name for the typed value itself.
        filesystem_friendly = self.benchmark.replace(" ", "_").replace("-", "_")
        if filesystem_friendly not in raw_names:
            raw_names.append(filesystem_friendly)
        for name in raw_names:
            if (catalog_dir / name).is_dir():
                return name
        return None

    def run(self, ctx: dict[str, Any]) -> dict[str, Any]:
        catalog_dir = self.resolve_catalog_dir(ctx)
        asof = ctx.get("asof")
        asof_date = asof if isinstance(asof, dt.date) else None
        symbol = self._resolve_benchmark_symbol(catalog_dir)

        regime: dict[str, Any] = {
            "benchmark": self.benchmark,
            "symbol": symbol,
            "condition": self.condition,
            "passed": True,
            "reason": "",
        }

        if symbol is None:
            regime["reason"] = "benchmark not found in catalog; passing through"
            logger.warning(
                "%s: benchmark %r not found in catalog; passing through", self.name, self.benchmark
            )
            ctx["regime"] = regime
            return ctx

        reading = latest_close_and_sma(catalog_dir, symbol, asof_date, self.window)
        if reading is None or reading[2] < self.window:
            regime["reason"] = "insufficient benchmark history; passing through"
            logger.warning("%s: insufficient history for %s; passing through", self.name, symbol)
            ctx["regime"] = regime
            return ctx

        close, sma, _ = reading
        above = close > sma
        passed = above if self.direction == "above" else not above
        regime.update({"close": close, "sma": sma, "above_sma": above, "passed": passed})
        if not passed:
            regime["reason"] = f"benchmark not {self.direction} SMA{self.window}"
            ctx["symbols"] = []
            if isinstance(ctx.get("candidates"), list):
                ctx["candidates"] = []
        ctx["regime"] = regime
        return ctx


class LiquidityFilter(Step):
    """Drop symbols whose average daily turnover is below ``min_avg_turnover`` (in INR)."""

    name = "liquidity"

    def __init__(
        self,
        min_avg_turnover: float = 5e7,
        lookback_days: int = 30,
        catalog_dir: Path | str | None = None,
    ) -> None:
        self.min_avg_turnover = min_avg_turnover
        self.lookback_days = lookback_days
        self.catalog_dir = Path(catalog_dir) if catalog_dir is not None else None

    def resolve_catalog_dir(self, ctx: dict[str, Any]) -> Path:
        if self.catalog_dir is not None:
            return find_catalog_path(self.catalog_dir)
        existing = ctx.get("catalog_dir")
        if isinstance(existing, Path):
            return existing
        return find_catalog_path()

    def run(self, ctx: dict[str, Any]) -> dict[str, Any]:
        symbols = list(ctx.get("symbols") or [])
        if not symbols:
            return ctx
        catalog_dir = self.resolve_catalog_dir(ctx)
        asof = ctx.get("asof")
        asof_date = asof if isinstance(asof, dt.date) else None
        turnovers = average_daily_turnover(catalog_dir, symbols, asof_date, self.lookback_days)
        keep = {s for s in symbols if turnovers.get(s, 0.0) >= self.min_avg_turnover}
        logger.info("%s: kept %d/%d symbols", self.name, len(keep), len(symbols))
        ctx["turnover"] = turnovers
        return _filter_candidates(ctx, keep)
