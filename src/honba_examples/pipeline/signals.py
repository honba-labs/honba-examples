"""honba_examples.pipeline.signals: timing signal steps.

Timing signals sit between the filters and the sizers, annotating the context with the entry
rule in use. In this phase ``BuyAndHoldSignal`` is a pass-through (hold every qualified symbol)
while ``BreakoutSignal`` and ``PullbackSignal`` are configuration stubs whose bar-level
filtering lands in a later phase.
"""

from __future__ import annotations

from typing import Any

from honba_examples.pipeline.base import Step

__all__ = ["BreakoutSignal", "BuyAndHoldSignal", "PullbackSignal", "SignalStep"]


class SignalStep(Step):
    """Base class for timing signal steps (pass-through by default)."""

    name = "signal"

    def __init__(self, **config: Any) -> None:
        self.config = dict(config)

    def run(self, ctx: dict[str, Any]) -> dict[str, Any]:
        ctx["signal"] = self.name
        if self.config:
            ctx["signal_config"] = dict(self.config)
        return ctx


class BuyAndHoldSignal(SignalStep):
    """Hold every qualified symbol for the whole backtest window (no timing filter)."""

    name = "buy_and_hold"


class BreakoutSignal(SignalStep):
    """Stub breakout signal: buy when price clears a rolling high (filtering not yet wired)."""

    name = "breakout"

    def __init__(self, lookback: int = 20, buffer_pct: float = 0.0) -> None:
        super().__init__(lookback=lookback, buffer_pct=buffer_pct)


class PullbackSignal(SignalStep):
    """Stub pullback signal: buy strength after a shallow retracement (not yet wired)."""

    name = "pullback"

    def __init__(self, lookback: int = 10, max_pullback_pct: float = 5.0) -> None:
        super().__init__(lookback=lookback, max_pullback_pct=max_pullback_pct)
