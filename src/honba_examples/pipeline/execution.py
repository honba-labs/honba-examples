"""honba_examples.pipeline.execution: backtest execution harnesses.

``SipBacktestHarness`` wraps :func:`honba_examples.sip.simulate_portfolio_sip`, driving a forward
monthly rupee-cost-averaging simulation over the qualified symbols and weights carried on the
context, and writing ``{"summary": ..., "stocks": [...]}`` back onto it.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Any

from honba_examples.pipeline.base import Step, as_date
from honba_examples.pipeline.manifest import manifest, param
from honba_examples.screening import find_catalog_path, get_latest_market_date
from honba_examples.sip import simulate_portfolio_sip

logger = logging.getLogger(__name__)

__all__ = ["SipBacktestHarness"]


class SipBacktestHarness(Step):
    """Run a forward SIP / lump-sum backtest over the pipeline's qualified symbols."""

    name = "sip_backtest"

    def __init__(
        self,
        asof: str | dt.date | None = None,
        end_date: str | dt.date | None = None,
        initial_capital: float | None = None,
        monthly_sip: float | None = None,
        allocation: str = "equal",
        sip_mode: str = "portfolio",
        data_dir: Path | str | None = None,
        catalog_dir: Path | str | None = None,
    ) -> None:
        self.asof = asof
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.monthly_sip = monthly_sip
        self.allocation = allocation
        self.sip_mode = sip_mode
        self.data_dir = Path(data_dir) if data_dir is not None else None
        self.catalog_dir = Path(catalog_dir) if catalog_dir is not None else None

    @classmethod
    def describe(cls) -> dict[str, Any]:
        return manifest(
            "execute.sip_backtest",
            "Forward SIP backtest",
            "Rupee-cost-averaging simulation over the qualified symbols from the start date to the "
            "end date (latest catalog session by default), using the incoming weights.",
            {
                "asof": param("date", "Start date; defaults to the screen's date.", optional=True),
                "end_date": param(
                    "date", "End date; defaults to the latest catalog session.", optional=True
                ),
                "initial_capital": param(
                    "float",
                    "Lump sum invested at the start (INR).",
                    optional=True,
                    min=0.0,
                    max=1e12,
                ),
                "monthly_sip": param(
                    "float",
                    "Monthly SIP (INR); defaults to the sizer's amount.",
                    optional=True,
                    min=0.0,
                    max=1e9,
                ),
                "sip_mode": param(
                    "enum",
                    "Whether monthly_sip is for the whole portfolio or per stock.",
                    default="portfolio",
                    choices=["portfolio", "per-stock"],
                ),
            },
            ["weights"],
            "none",
            requires={"bars": ["close", "ts"], "pit": True},
            tags=["sip", "backtest"],
        )

    def resolve_catalog_dir(self, ctx: dict[str, Any]) -> Path:
        if self.catalog_dir is not None:
            return find_catalog_path(self.catalog_dir)
        existing = ctx.get("catalog_dir")
        if isinstance(existing, Path):
            return existing
        if self.data_dir is not None:
            return find_catalog_path(self.data_dir)
        return find_catalog_path()

    def resolve_asof(self, ctx: dict[str, Any], catalog_dir: Path) -> dt.date:
        asof = as_date(self.asof) or as_date(ctx.get("asof"))
        return asof if asof is not None else get_latest_market_date(catalog_dir)

    def resolve_end(self, ctx: dict[str, Any], catalog_dir: Path, asof: dt.date) -> dt.date:
        end = as_date(self.end_date) or as_date(ctx.get("end_date"))
        if end is not None:
            return end
        latest = get_latest_market_date(catalog_dir)
        return max(asof, latest)

    def _build_weights(
        self, candidates: list[dict[str, Any]], symbols: list[str]
    ) -> dict[str, float] | None:
        if not symbols or self.allocation == "equal":
            return None
        if self.allocation == "score":
            raw = {c["symbol"]: max(float(c.get("mb_score", 10.0)), 1.0) for c in candidates}
        elif self.allocation == "52w-ratio":
            raw = {
                c["symbol"]: 1.0 / max(abs(float(c.get("pct_from_high", -5.0))), 0.1)
                for c in candidates
            }
        else:
            return None
        total = sum(raw.values())
        if total <= 0:
            return None
        return {symbol: raw.get(symbol, 0.0) / total for symbol in symbols}

    def run(self, ctx: dict[str, Any]) -> dict[str, Any]:
        catalog_dir = self.resolve_catalog_dir(ctx)
        asof = self.resolve_asof(ctx, catalog_dir)
        end = self.resolve_end(ctx, catalog_dir, asof)
        symbols = list(ctx.get("symbols") or [])

        weights = ctx.get("weights")
        if not isinstance(weights, dict) or not weights:
            weights = self._build_weights(ctx.get("candidates") or [], symbols)

        monthly_sip = self.monthly_sip
        if monthly_sip is None:
            monthly_sip = ctx.get("monthly_amount")

        initial_capital = self.initial_capital
        if initial_capital is None:
            initial_capital = ctx.get("initial_capital")

        logger.info("%s: %d symbols from %s to %s", self.name, len(symbols), asof, end)

        results = simulate_portfolio_sip(
            catalog_dir=catalog_dir,
            symbols=symbols,
            start_date=asof,
            end_date=end,
            initial_capital=initial_capital,
            monthly_sip=monthly_sip,
            sip_mode=self.sip_mode,
            weights=weights,
        )

        ctx["summary"] = results["summary"]
        ctx["stocks"] = results["stocks"]
        ctx["backtest"] = results
        ctx["asof"] = asof
        ctx["end_date"] = end
        return ctx
