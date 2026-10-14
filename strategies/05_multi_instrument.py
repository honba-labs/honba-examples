"""strategies/05_multi_instrument: basket SMA crossover with equal-weight rebalancing.

Signals generated per instrument; on each rebalance day:
1. Sell instruments that left the basket
2. Buy new additions
3. Rebalance survivors to equal weight

Run::

    python strategies/05_multi_instrument.py

Pitfalls::

    - Nothing is rebalanced: _rebalance only records _positions_before and sends no orders, so the
      default run is four one-share buys, one per symbol. The docstring's sell leavers, buy joiners
      and equalise weights is not implemented, and a rebalancer that never trades cannot be judged.
    - Counter semantics: _bar_count increments for every warmed-up bar of every symbol, so
      rebalance_every=20 means 20 bars across the four-symbol basket (about five sessions), not 20
      sessions.
    - Identical series: all four symbols get the same 2500 + 0.5 x i path, so there is no dispersion
      or correlation structure. Diversification, and the correlated drawdowns that cut it, cannot
      show up.
    - Basket picked today: RELIANCE, TCS, INFY and HDFCBANK are present-day large caps chosen with
      hindsight. With real data the basket must be the constituents as of each date, otherwise
      survivorship bias flatters the result.
    - One share each is not equal weight in rupees (prices differ), fills are at the signal bar's
      close (fill_delay=0), and there are no costs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma

from honba_examples.cli import run_example
from honba_examples.replay_report import replay_report
from honba_examples.synthetic import bar
from honba_examples.synthetic import weekdays as weekdays_func

__all__ = ["main", "run"]


class BasketSma(Strategy):
    """Equal-weight SMA crossover across a basket of symbols."""

    name = "basket_sma"
    warmup_bars = 30
    rebalance_every: int = 20

    def __init__(
        self,
        fast: int = 10,
        slow: int = 30,
        rebalance_every: int = 20,
        symbols: tuple[str, ...] = ("RELIANCE", "TCS", "INFY", "HDFCBANK"),
    ) -> None:
        super().__init__()
        self.fast = fast
        self.slow = slow
        self.rebalance_every = rebalance_every
        self.symbols = symbols
        self._fast_sma: dict[str, Sma] = {}
        self._slow_sma: dict[str, Sma] = {}
        self._bar_count = 0
        self._positions_before: dict[str, float] = {}

    def on_bar(self, bar) -> None:
        sym = bar.instrument_id.symbol

        # Initialize indicators for this symbol
        if sym not in self._fast_sma:
            self._fast_sma[sym] = Sma(self.fast)
            self._slow_sma[sym] = Sma(self.slow)

        fast_val = self._fast_sma[sym].update(bar.close)
        slow_val = self._slow_sma[sym].update(bar.close)

        if fast_val is None or slow_val is None:
            return

        self._bar_count += 1
        position = self.position(bar.instrument_id)

        # Signal per instrument
        if fast_val > slow_val and position <= 0:
            self.buy(bar.instrument_id, 1)
        elif fast_val < slow_val and position > 0:
            self.sell(bar.instrument_id, position)

        # Rebalance every N bars
        if self._bar_count % self.rebalance_every == 0:
            self._rebalance()

    def _rebalance(self) -> None:
        """Equal-weight the basket: track changes and adjust."""
        positions = {}
        for sym in self.symbols:
            iid = self._instrument(sym)
            if iid is not None:
                positions[sym] = self.position(iid)
        self._positions_before = positions

    def _instrument(self, symbol: str):
        for iid in self.ctx.positions():
            if iid.symbol == symbol:
                return iid
        return None


def run(
    bars: int = 100,
    fast: int = 10,
    slow: int = 30,
    rebalance_every: int = 20,
) -> dict[str, Any]:
    symbols = ("RELIANCE", "TCS", "INFY", "HDFCBANK")
    start = dt.date(2026, 1, 1)
    bars_list = []
    for i, d in enumerate(weekdays_func(start, bars)):
        for sym in symbols:
            bars_list.append(bar(sym, d, 2500.0 + i * 0.5))
    bars_list.sort(key=lambda b: (b.ts, b.instrument_id.symbol))

    strat = BasketSma(fast=fast, slow=slow, rebalance_every=rebalance_every)
    return replay_report(
        strat,
        bars_list,
        {
            "fast": fast,
            "slow": slow,
            "rebalance_every": rebalance_every,
            "symbols": list(symbols),
        },
        config_extra={"bars": bars},
    )


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bars", type=int, default=100)
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--rebalance-every", type=int, default=20)


def main(argv: list[str] | None = None) -> int:
    return run_example(run, __doc__, _add_args, argv=argv)


if __name__ == "__main__":
    sys.exit(main())
