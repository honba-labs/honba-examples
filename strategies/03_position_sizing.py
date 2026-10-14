"""strategies/03_position_sizing: compare fixed, volatility and Kelly sizing.

Three sizing methods on the same SMA crossover:
1. Fixed: same quantity every trade
2. Volatility (ATR): risk = equity * risk_pct / (2 * ATR)
3. Kelly fraction: f* = (bp - q) / b where p=win rate, b=avg win/loss

Uses ``honba.strategies.sizing`` helpers and synthetic bars.

Run::

    python strategies/03_position_sizing.py

Pitfalls::

    - The strategy never places an order: the loop for fill in self.drain_intents(): pass at the end
      of on_bar consumes the intents just emitted, so replay finds none. Every sizing mode prints
      intents [] and fills []. A silent no-op looks like success, so assert a minimum trade count.
    - Kelly is a stub: sizing='kelly' falls back to fixed_qty. The real formula needs a win rate and
      payoff ratio estimated from past trades; from a few trades those estimates are noisy and full
      Kelly overbets, so practitioners use a fraction of it.
    - Judge sizers on risk, not final P&L: the same signals with a bigger size always make or lose
      more. Compare drawdown and risk-adjusted return, and remember sizing was computed from the
      signal bar's close and filled at it (fill_delay=0), so a gap changes the risk you really took.
    - Volatility sizing on synthetic data explodes: the ramp has ATR 0.5, so int(equity x risk_pct /
      (2 x ATR)) is 1000 shares, about 2.5 million INR against a hard-coded 100000 equity. Real
      sizing needs a cap by capital and by liquidity.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba.strategies.base import Strategy
from honba.strategies.indicators import Atr, Sma

from honba_examples.cli import run_example
from honba_examples.replay_report import replay_report
from honba_examples.synthetic import trending_bars

__all__ = ["main", "run"]


class SmaSizing(Strategy):
    """SMA crossover with pluggable position sizing."""

    name = "sma_sizing"
    warmup_bars = 30

    def __init__(
        self,
        fast: int = 10,
        slow: int = 30,
        sizing: str = "fixed",
        fixed_qty: float = 1.0,
        risk_pct: float = 0.01,
        kelly_lookback: int = 20,
    ) -> None:
        super().__init__()
        self.fast_sma = Sma(fast)
        self.slow_sma = Sma(slow)
        self.atr = Atr(14)
        self.sizing = sizing
        self.fixed_qty = fixed_qty
        self.risk_pct = risk_pct
        self.kelly_lookback = kelly_lookback
        self._trade_pnl: list[float] = []

    def on_bar(self, bar) -> None:
        close = bar.close
        fast_val = self.fast_sma.update(close)
        slow_val = self.slow_sma.update(close)
        atr_val = self.atr.update(bar.high, bar.low, bar.close)

        if fast_val is None or slow_val is None or atr_val is None:
            return

        position = self.position(bar.instrument_id)

        # Entry
        if fast_val > slow_val and position <= 0:
            qty = self._size(atr_val, close)
            if qty > 0:
                self.buy(bar.instrument_id, qty)

        # Exit
        elif fast_val < slow_val and position > 0:
            self.sell(bar.instrument_id, position)

        # Track PnL for Kelly
        for fill in self.drain_intents():
            pass  # replay doesn't expose fills this way; Kelly is illustrative

    def _size(self, atr_val: float, price: float) -> int:
        equity = 100_000.0
        if self.sizing == "fixed":
            return int(self.fixed_qty)
        elif self.sizing == "volatility":
            risk_amount = equity * self.risk_pct
            return max(1, int(risk_amount / (atr_val * 2)))
        elif self.sizing == "kelly":
            # kelly_fraction not available; use fixed qty as fallback
            return int(self.fixed_qty)
        return int(self.fixed_qty)


def run(
    bars: int = 100,
    fast: int = 10,
    slow: int = 30,
    sizing: str = "fixed",
    fixed_qty: float = 1.0,
    risk_pct: float = 0.01,
) -> dict[str, Any]:
    bars_list = trending_bars("RELIANCE", bars)

    strat = SmaSizing(fast=fast, slow=slow, sizing=sizing, fixed_qty=fixed_qty, risk_pct=risk_pct)
    return replay_report(
        strat,
        bars_list,
        {
            "fast": fast,
            "slow": slow,
            "sizing": sizing,
            "fixed_qty": fixed_qty,
            "risk_pct": risk_pct,
        },
        config_extra={"bars": bars},
    )


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bars", type=int, default=100)
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--sizing", choices=["fixed", "volatility", "kelly"], default="fixed")
    parser.add_argument("--fixed-qty", type=float, default=1.0)
    parser.add_argument("--risk-pct", type=float, default=0.01)


def main(argv: list[str] | None = None) -> int:
    return run_example(run, __doc__, _add_args, argv=argv)


if __name__ == "__main__":
    sys.exit(main())
