"""strategies/01_first_strategy: a minimal SMA crossover strategy.

Subclass ``honba.strategies.base.Strategy``, set ``name`` and ``warmup_bars``, and
implement ``on_bar``. The strategy emits market orders through ``self.buy``/
``self.sell`` (or ``self.submit`` for limit/stop variants). The runner binds a
context, calls ``on_start`` → streams bars → ``on_stop``.

This example runs a fast SMA(10) vs slow SMA(30) crossover on a synthetic
100-bar series (see ``honba_examples/synthetic.py``) using the testing harness
``honba.strategies.testing.replay`` — no adapter, no Parquet store, no network.

Run::

    python strategies/01_first_strategy.py
    python strategies/01_first_strategy.py --out first_strategy.json

Pitfalls::

    - Fill timing: replay_report calls replay with its default fill_delay=0, so each order fills at
      the close of the bar whose close produced the signal. You cannot trade a close you have just
      observed; the realistic model is the next bar's open (fill_delay=1, or the engine's
      NextOpenExecution).
    - State test, not an event: entry is fast > slow while flat, so on the synthetic ramp (always
      up) the first warmed-up bar buys straight away. Results depend on where the sample starts; try
      several start dates on real data.
    - Short from flat: the sell branch tests position >= 0, so a down-state while flat opens a short
      instead of only exiting a long. Overnight shorts in Indian cash equities are not possible
      without F&O or SLB, and borrow cost is not modelled. The backtesting/ variants exit longs
      only.
    - Perfect-trend data: trending_bars is base + 0.5 x i with no noise, gaps or drawdowns, so a
      trend follower looks flawless and trades once (one buy, no sell by default). Costs and
      slippage are zero.
    - Defence shown: warmup_bars=30 plus the early return while either Sma is None keeps signals
      from partial windows.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma

from honba_examples.cli import run_example
from honba_examples.replay_report import replay_report
from honba_examples.synthetic import trending_bars

__all__ = ["main", "run"]


class SmaCrossover(Strategy):
    """Fast SMA crossing above/below slow SMA → buy/sell one unit."""

    name = "sma_crossover"
    warmup_bars = 30

    def __init__(self, fast: int = 10, slow: int = 30, quantity: float = 1.0) -> None:
        super().__init__()
        self.fast = Sma(fast)
        self.slow = Sma(slow)
        self.quantity = quantity

    def on_bar(self, bar) -> None:
        fast_val = self.fast.update(bar.close)
        slow_val = self.slow.update(bar.close)

        if fast_val is None or slow_val is None:
            return  # still warming up

        position = self.position(bar.instrument_id)
        if fast_val > slow_val and position <= 0:
            self.buy(bar.instrument_id, self.quantity)
        elif fast_val < slow_val and position >= 0:
            self.sell(bar.instrument_id, self.quantity)


def run(
    bars: int = 100,
    fast: int = 10,
    slow: int = 30,
    quantity: float = 1.0,
) -> dict[str, Any]:
    """Run the strategy on synthetic bars and return a JSON-serializable report."""

    bars_list = trending_bars("RELIANCE", bars)

    strat = SmaCrossover(fast=fast, slow=slow, quantity=quantity)
    return replay_report(
        strat,
        bars_list,
        {
            "fast": fast,
            "slow": slow,
            "quantity": quantity,
        },
        config_extra={"bars": bars},
    )


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bars", type=int, default=100, help="number of synthetic bars")
    parser.add_argument("--fast", type=int, default=10, help="fast SMA period")
    parser.add_argument("--slow", type=int, default=30, help="slow SMA period")
    parser.add_argument("--quantity", type=float, default=1.0, help="position size per signal")


def main(argv: list[str] | None = None) -> int:
    return run_example(run, __doc__, _add_args, argv=argv)


if __name__ == "__main__":
    sys.exit(main())
