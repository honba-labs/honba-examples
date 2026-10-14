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

from honba.domain.order import OrderSide
from honba.strategies.base import Strategy
from honba.strategies.indicators import Atr, Sma

from honba_examples.cli import run_example
from honba_examples.replay_report import replay_report
from honba_examples.synthetic import oscillating_bars

__all__ = ["main", "run"]


def fractional_kelly(
    returns: list[float], fraction: float = 0.5, cap: float = 0.25, min_trades: int = 3
) -> float | None:
    """Fractional Kelly: the share of equity to commit, from past per-trade returns.

    ``f* = p - (1 - p) / b`` with ``p`` the win rate and ``b`` the average win over the average
    loss (both from ``returns``). The result is ``fraction * f*`` clamped to ``[0, cap]``.
    Returns ``None`` with fewer than ``min_trades`` returns (no usable estimate), ``0.0`` when
    the estimated edge is not positive, and ``cap`` when there is no loss to measure ``b`` against.
    """
    if len(returns) < min_trades:
        return None
    wins = [r for r in returns if r > 0]
    losses = [-r for r in returns if r < 0]
    if not wins:
        return 0.0
    if not losses:
        return cap
    p = len(wins) / len(returns)
    b = (sum(wins) / len(wins)) / (sum(losses) / len(losses))
    full = p - (1.0 - p) / b
    return min(cap, max(0.0, fraction * full))


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
        kelly_fraction: float = 0.5,
        kelly_cap: float = 0.25,
        kelly_min_trades: int = 3,
        equity: float = 100_000.0,
    ) -> None:
        super().__init__()
        self.fast_sma = Sma(fast)
        self.slow_sma = Sma(slow)
        self.atr = Atr(14)
        self.sizing = sizing
        self.fixed_qty = fixed_qty
        self.risk_pct = risk_pct
        self.kelly_lookback = kelly_lookback
        self.kelly_fraction = kelly_fraction
        self.kelly_cap = kelly_cap
        self.kelly_min_trades = kelly_min_trades
        self.equity = equity
        self._entry_price: float | None = None
        self._trade_returns: list[float] = []  # closed round trips: exit / entry - 1

    def on_fill(self, fill) -> None:
        if fill.side is OrderSide.BUY:
            self._entry_price = fill.price
        elif self._entry_price:
            self._trade_returns.append(fill.price / self._entry_price - 1.0)
            self._entry_price = None

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

    def _size(self, atr_val: float, price: float) -> int:
        """Shares to buy, never more than ``equity`` can pay for (no leverage)."""
        if self.sizing == "volatility":
            qty = max(1, int(self.equity * self.risk_pct / (atr_val * 2)))
        elif self.sizing == "kelly":
            recent = self._trade_returns[-self.kelly_lookback :]
            f = fractional_kelly(recent, self.kelly_fraction, self.kelly_cap, self.kelly_min_trades)
            qty = int(self.fixed_qty) if f is None else int(self.equity * f / price)
        else:
            qty = int(self.fixed_qty)
        return min(qty, int(self.equity // price))


def run(
    bars: int = 200,
    fast: int = 10,
    slow: int = 30,
    sizing: str = "fixed",
    fixed_qty: float = 1.0,
    risk_pct: float = 0.01,
) -> dict[str, Any]:
    bars_list = oscillating_bars("RELIANCE", bars, amplitude=60.0, drift=4.0)

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
    parser.add_argument("--bars", type=int, default=200)
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--sizing", choices=["fixed", "volatility", "kelly"], default="fixed")
    parser.add_argument("--fixed-qty", type=float, default=1.0)
    parser.add_argument("--risk-pct", type=float, default=0.01)


def main(argv: list[str] | None = None) -> int:
    return run_example(run, __doc__, _add_args, argv=argv)


if __name__ == "__main__":
    sys.exit(main())
