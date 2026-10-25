"""strategies/12_buy_and_hold_lifecycle: a minimal buy and hold lifecycle strategy.

Demonstrates the lifecycle hooks: ``on_start``, ``on_bar``, and ``on_stop``.
Subclass ``honba.strategies.base.Strategy`` and implement these methods.

Run::

    python examples/strategies/12_buy_and_hold_lifecycle.py

Pitfalls::

    - Passive holding has no drawdown protection: buy-and-hold accepts the full downside
      of the underlying instrument with zero risk management, circuit band logic, or stop-loss.
    - Timing risk on entry: buying on the very first bar subjects the portfolio to high
      point-in-time entry variance depending on the starting date of the backtest.
    - Zero capital reallocation: holds a static quantity without dynamic capital sizing,
      dividend reinvestment, or multi-asset cash management.
    - Lifecycle hooks in production: ``on_start`` and ``on_stop`` execute at lifecycle boundaries;
      ungraceful termination (e.g. process crash or host failure) may prevent ``on_stop`` from running.
"""

from __future__ import annotations

import argparse
import sys
from typing import ClassVar, override

from honba.domain.bar import Bar
from honba.strategies.base import Strategy

from honba_examples.cli import run_example
from honba_examples.replay_report import replay_report
from honba_examples.synthetic import trending_bars

__all__ = ["main", "run"]


class BuyAndHold(Strategy):
    """Buy one unit on the first bar and hold."""

    name: ClassVar[str] = "buy_and_hold_lifecycle"
    quantity: float
    bought: bool

    def __init__(self, quantity: float = 1.0) -> None:
        super().__init__()
        self.quantity = quantity
        self.bought = False

    @override
    def on_start(self) -> None:
        print("Strategy starting")
        super().on_start()

    @override
    def on_bar(self, bar: Bar) -> None:
        if not self.bought:
            print(f"Buying on bar: {bar.ts}")
            self.buy(bar.instrument_id, self.quantity)
            self.bought = True

    @override
    def on_stop(self) -> None:
        print("Strategy stopping")
        super().on_stop()


def run(
    bars: int = 100,
    quantity: float = 1.0,
) -> dict[str, object]:
    """Run the strategy on synthetic bars and return a JSON-serializable report."""

    bars_list = trending_bars("RELIANCE", bars)

    strat = BuyAndHold(quantity=quantity)
    return replay_report(
        strat,
        bars_list,
        {
            "quantity": quantity,
        },
        config_extra={"bars": bars},
    )


def _add_args(parser: argparse.ArgumentParser) -> None:
    _ = parser.add_argument("--bars", type=int, default=100, help="number of synthetic bars")
    _ = parser.add_argument("--quantity", type=float, default=1.0, help="position size")


def main(argv: list[str] | None = None) -> int:
    return run_example(run, __doc__, _add_args, argv=argv)


if __name__ == "__main__":
    sys.exit(main())
