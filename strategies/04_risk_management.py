"""strategies/04_risk_management: max drawdown, daily loss limit, position limits.

Risk controls enforced inside the strategy (not the engine):
- Max portfolio drawdown from peak equity
- Max daily loss
- Max position size per instrument
- Max sector exposure (simplified: single instrument)

Run::

    python strategies/04_risk_management.py

Pitfalls::

    - The equity estimate is wrong: _estimate_equity returns 100000 plus the market value of
      positions and never subtracts the cash spent buying them, so equity is inflated by the cost
      basis. Drawdown, daily-loss and position-weight checks run on a number that does not track the
      account.
    - A cap that also blocks exits: the position_pct >= max_position_pct check returns before the
      sell branch, so once a winner grows past the cap the strategy ignores its own exit signal. A
      guard against adding should not stop selling.
    - One bar is one day: _current_day changes with every bar timestamp, so the daily loss limit is
      a single close-to-close move on daily data. Intraday gaps and the price you actually exit at
      are not seen; the flatten order fills at the same close that triggered it.
    - Guards that never fire: the synthetic ramp never draws down, so by default there is one buy of
      seven shares and no guard triggers. Test risk rules on falling and gappy series, otherwise
      they are untested code.
    - Starting equity and peak are constants (100000), not the account. Limits expressed as
      percentages of a made-up base give a false sense of safety.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba.domain.order import OrderSide
from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma

from honba_examples.cli import run_example
from honba_examples.replay_report import replay_report
from honba_examples.synthetic import trending_bars

__all__ = ["main", "run"]


class RiskManagedSma(Strategy):
    """SMA crossover with risk guards."""

    name = "risk_managed_sma"
    warmup_bars = 30

    def __init__(
        self,
        fast: int = 10,
        slow: int = 30,
        max_drawdown_pct: float = 0.10,
        max_daily_loss_pct: float = 0.02,
        max_position_pct: float = 0.20,
    ) -> None:
        super().__init__()
        self.fast_sma = Sma(fast)
        self.slow_sma = Sma(slow)
        self.max_drawdown_pct = max_drawdown_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_position_pct = max_position_pct
        self._peak_equity = 100_000.0
        self._day_start_equity = 100_000.0
        self._current_day = None
        self._last_equity: float | None = None
        self._cash = 100_000.0

    def on_fill(self, fill) -> None:
        sign = -1.0 if fill.side is OrderSide.BUY else 1.0
        self._cash += sign * fill.quantity * fill.price

    def on_bar(self, bar) -> None:
        close = bar.close
        fast_val = self.fast_sma.update(close)
        slow_val = self.slow_sma.update(close)

        if fast_val is None or slow_val is None:
            return

        # Track equity and daily reset
        equity = self._estimate_equity(close)
        if self._current_day != bar.ts:
            self._current_day = bar.ts
            # previous close-to-close baseline: a bar's own equity would make the loss always 0
            self._day_start_equity = self._last_equity if self._last_equity is not None else equity
        self._last_equity = equity
        self._peak_equity = max(self._peak_equity, equity)

        # Risk checks
        drawdown = (self._peak_equity - equity) / self._peak_equity
        daily_loss = (self._day_start_equity - equity) / self._day_start_equity
        position = self.position(bar.instrument_id)
        position_pct = abs(position * close) / equity if equity > 0 else 0

        if drawdown >= self.max_drawdown_pct:
            self._flatten_all()
            return
        if daily_loss >= self.max_daily_loss_pct:
            self._flatten_all()
            return

        # Exits are never blocked by the size cap; the cap only stops new entries
        if fast_val < slow_val and position > 0:
            self.sell(bar.instrument_id, position)
        elif fast_val > slow_val and position <= 0 and position_pct < self.max_position_pct:
            qty = self._size(close, equity)
            if qty > 0:
                self.buy(bar.instrument_id, qty)

    def _estimate_equity(self, price: float) -> float:
        """Rough equity estimate from cash + position value."""
        equity = self._cash  # starting cash, less what buys cost, plus what sells returned
        for qty in self.ctx.positions().values():
            equity += qty * price
        return equity

    def _size(self, price: float, equity: float) -> int:
        max_qty = int(equity * self.max_position_pct / price)
        return max(1, max_qty)

    def _flatten_all(self) -> None:
        for iid, qty in self.ctx.positions().items():
            if qty > 0:
                self.sell(iid, qty)


def run(
    bars: int = 100,
    fast: int = 10,
    slow: int = 30,
    max_drawdown_pct: float = 0.10,
    max_daily_loss_pct: float = 0.02,
    max_position_pct: float = 0.20,
) -> dict[str, Any]:
    bars_list = trending_bars("RELIANCE", bars)

    strat = RiskManagedSma(
        fast=fast,
        slow=slow,
        max_drawdown_pct=max_drawdown_pct,
        max_daily_loss_pct=max_daily_loss_pct,
        max_position_pct=max_position_pct,
    )
    return replay_report(
        strat,
        bars_list,
        {
            "fast": fast,
            "slow": slow,
            "max_drawdown_pct": max_drawdown_pct,
            "max_daily_loss_pct": max_daily_loss_pct,
            "max_position_pct": max_position_pct,
        },
        config_extra={"bars": bars},
    )


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bars", type=int, default=100)
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--max-drawdown-pct", type=float, default=0.10)
    parser.add_argument("--max-daily-loss-pct", type=float, default=0.02)
    parser.add_argument("--max-position-pct", type=float, default=0.20)


def main(argv: list[str] | None = None) -> int:
    return run_example(run, __doc__, _add_args, argv=argv)


if __name__ == "__main__":
    sys.exit(main())
