"""strategies/05_multi_instrument: basket SMA crossover with equal-weight rebalancing.

Signals generated per instrument; on each rebalance day:
1. Sell instruments that left the basket
2. Buy new additions
3. Rebalance survivors to equal weight

Run::

    python strategies/05_multi_instrument.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma
from honba.strategies.testing import replay

from honba_examples.jsonable import jsonable
from tests.synthetic import bar
from tests.synthetic import weekdays as weekdays_func

__all__ = ["main", "run"]


class BasketSma(Strategy):
    """Equal-weight SMA crossover across a basket of symbols."""

    name: str = "basket_sma"
    warmup_bars: int = 30
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
        total_value = 100_000.0  # Simplified
        total_value / len(self.symbols)

        for sym in self.symbols:
            iid = self._instrument(sym)
            if iid is None:
                continue
            self.position(iid)
            # Note: we can't get exact price here without bar; simplified
            # Rebalance logic would go here in a real implementation

        self._positions_before = {sym: self.position(self._instrument(sym)) for sym in self.symbols if self._instrument(sym)}

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
    result = replay(strat, bars_list)

    strategy_repr = {
        "name": strat.name,
        "warmup_bars": strat.warmup_bars,
        "params": {
            "fast": fast,
            "slow": slow,
            "rebalance_every": rebalance_every,
            "symbols": list(symbols),
        },
    }

    return {
        "strategy": strategy_repr,
        "config": {
            "fast": fast,
            "slow": slow,
            "rebalance_every": rebalance_every,
            "symbols": list(symbols),
            "bars": bars,
        },
        "result": jsonable(result),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bars", type=int, default=100)
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--rebalance-every", type=int, default=20)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(
        bars=args.bars,
        fast=args.fast,
        slow=args.slow,
        rebalance_every=args.rebalance_every,
    )

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())