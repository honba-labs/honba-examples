"""honba_examples.pipeline.sizing: allocation and position sizing steps.

Sizers consume the qualified symbols on the context and produce ``{"weights": {symbol: float}}``
with weights summing to 1.0. ``FixedSipSizer`` also records the per-stock monthly amount so a
downstream :class:`~honba_examples.pipeline.execution.SipBacktestHarness` can pick it up.
"""

from __future__ import annotations

import datetime as dt
import logging
import statistics
from pathlib import Path
from typing import Any

from honba_examples.pipeline.base import Step
from honba_examples.pipeline.screeners import realized_volatility
from honba_examples.screening import find_catalog_path

logger = logging.getLogger(__name__)

__all__ = ["EqualWeightSizer", "FixedSipSizer", "InverseVolatilitySizer", "SizerStep"]


def _normalize(raw: dict[str, float]) -> dict[str, float]:
    positives = {symbol: value for symbol, value in raw.items() if value > 0}
    total = sum(positives.values())
    if total <= 0:
        return {}
    return {symbol: value / total for symbol, value in positives.items()}


class SizerStep(Step):
    """Base class for allocation steps."""

    name = "sizer"

    def compute_weights(self, symbols: list[str], ctx: dict[str, Any]) -> dict[str, float]:
        raise NotImplementedError("Sizers must implement compute_weights()")

    def run(self, ctx: dict[str, Any]) -> dict[str, Any]:
        symbols = list(ctx.get("symbols") or [])
        ctx["weights"] = self.compute_weights(symbols, ctx)
        return ctx


class FixedSipSizer(SizerStep):
    """Equal (or custom) allocation paired with a fixed monthly SIP amount per portfolio."""

    name = "fixed_sip"

    def __init__(
        self,
        monthly_amount: float = 10000.0,
        weights: dict[str, float] | None = None,
    ) -> None:
        self.monthly_amount = float(monthly_amount)
        self.custom_weights = dict(weights) if weights else None

    def compute_weights(self, symbols: list[str], ctx: dict[str, Any]) -> dict[str, float]:
        if not symbols:
            return {}
        if self.custom_weights:
            raw = {s: float(self.custom_weights.get(s, 0.0)) for s in symbols}
        else:
            raw = {s: 1.0 for s in symbols}
        return _normalize(raw)

    def run(self, ctx: dict[str, Any]) -> dict[str, Any]:
        ctx = super().run(ctx)
        ctx["monthly_amount"] = self.monthly_amount
        return ctx


class EqualWeightSizer(SizerStep):
    """Assign equal 1/N weights across every qualified symbol."""

    name = "equal_weight"

    def compute_weights(self, symbols: list[str], ctx: dict[str, Any]) -> dict[str, float]:
        if not symbols:
            return {}
        return {symbol: 1.0 / len(symbols) for symbol in symbols}


class InverseVolatilitySizer(SizerStep):
    """Allocate inversely proportional to trailing realized volatility."""

    name = "inverse_volatility"

    def __init__(
        self,
        vol_window: int = 30,
        catalog_dir: Path | str | None = None,
    ) -> None:
        self.vol_window = vol_window
        self.catalog_dir = Path(catalog_dir) if catalog_dir is not None else None

    def resolve_catalog_dir(self, ctx: dict[str, Any]) -> Path:
        if self.catalog_dir is not None:
            return find_catalog_path(self.catalog_dir)
        existing = ctx.get("catalog_dir")
        if isinstance(existing, Path):
            return existing
        return find_catalog_path()

    def compute_weights(self, symbols: list[str], ctx: dict[str, Any]) -> dict[str, float]:
        if not symbols:
            return {}
        catalog_dir = self.resolve_catalog_dir(ctx)
        asof = ctx.get("asof")
        asof_date = asof if isinstance(asof, dt.date) else None
        vols = realized_volatility(catalog_dir, symbols, asof_date, self.vol_window)
        known = {s: v for s, v in vols.items() if v and v > 0}
        if not known:
            return {symbol: 1.0 / len(symbols) for symbol in symbols}
        fallback = statistics.median(known.values())
        raw = {symbol: 1.0 / (known.get(symbol) or fallback) for symbol in symbols}
        return _normalize(raw)
