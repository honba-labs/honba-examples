"""strategies/05_multi_instrument: basket SMA crossover with equal-weight rebalancing.

Signals are generated per instrument; a symbol is in the basket while its fast SMA is above its
slow SMA, and each member is sized to ``equity / n_symbols``:
1. Enter a member when its signal turns up (buy to the equal-weight target)
2. Exit it when the signal turns down
3. Every ``rebalance_every`` usable bars of a symbol, trim or top up that member to the target

Run::

    python strategies/05_multi_instrument.py

Pitfalls::

    - Rebalancing is per symbol, not per session: each member is re-sized on its own counter, using
      the latest close of the others (the previous close for symbols that come later in the same
      session). A real rebalance prices the whole book at one instant.
    - Counter semantics: the counter counts usable bars of that symbol, so rebalance_every=20 is 20
      sessions after warm-up. Members that left on a down signal are not re-bought until the
      signal turns up again, so cash can sit idle.
    - Synthetic ramps: the four symbols share the 2500 start but have different slopes, so weights
      drift and the rebalance has work to do, yet there is still no noise, dispersion of returns
      or correlation structure. Diversification, and the correlated drawdowns that cut it, cannot
      show up.
    - Basket picked today: RELIANCE, TCS, INFY and HDFCBANK are present-day large caps chosen with
      hindsight. With real data the basket must be the constituents as of each date, otherwise
      survivorship bias flatters the result.
    - Whole shares only, fills at the signal bar's close (fill_delay=0) and no costs: every
      rebalance trade is free here, but turnover is not free in a real account.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba.domain.order import OrderSide
from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma

from honba_examples.cli import run_example
from honba_examples.replay_report import replay_report
from honba_examples.synthetic import bar
from honba_examples.synthetic import weekdays as weekdays_func

__all__ = ["basket_bars", "main", "run"]


_DEFAULT_SYMBOLS = ("RELIANCE", "TCS", "INFY", "HDFCBANK")
_STEPS = (0.5, 0.25, 3.0, 1.5)  # per-symbol ramp slope, so the weights drift apart


def basket_bars(bars: int, symbols: tuple[str, ...] = _DEFAULT_SYMBOLS) -> list:
    """Daily bars for every symbol: the same 2500 start, a different ramp slope per symbol."""
    out = []
    for i, d in enumerate(weekdays_func(dt.date(2026, 1, 1), bars)):
        for k, sym in enumerate(symbols):
            out.append(bar(sym, d, 2500.0 + i * _STEPS[k % len(_STEPS)]))
    out.sort(key=lambda b: (b.ts, b.instrument_id.symbol))
    return out


class BasketSma(Strategy):
    """Equal-weight SMA crossover across a basket of symbols.

    A symbol is in the basket while its fast SMA is above its slow SMA. Each member is held at
    ``equity / len(symbols)``; on its own ``rebalance_every``-th usable bar it is trimmed or topped
    up to that target. Equity is ``capital`` plus fill cash flows plus every position at the
    latest close seen for its symbol (symbols later in a session still carry the prior close).
    """

    name = "basket_sma"
    warmup_bars = 30
    rebalance_every: int = 20

    def __init__(
        self,
        fast: int = 10,
        slow: int = 30,
        rebalance_every: int = 20,
        symbols: tuple[str, ...] = _DEFAULT_SYMBOLS,
        capital: float = 100_000.0,
    ) -> None:
        super().__init__()
        self.fast = fast
        self.slow = slow
        self.rebalance_every = rebalance_every
        self.symbols = symbols
        self._cash = capital
        self._fast_sma: dict[str, Sma] = {}
        self._slow_sma: dict[str, Sma] = {}
        self._bar_count: dict[str, int] = {}  # usable (post-warm-up) bars seen per symbol
        self._iids: dict[str, Any] = {}
        self._last_close: dict[str, float] = {}

    def on_fill(self, fill) -> None:
        sign = -1.0 if fill.side is OrderSide.BUY else 1.0
        self._cash += sign * fill.quantity * fill.price

    def on_bar(self, bar) -> None:
        sym = bar.instrument_id.symbol
        self._iids[sym] = bar.instrument_id
        self._last_close[sym] = bar.close

        # Initialize indicators for this symbol
        if sym not in self._fast_sma:
            self._fast_sma[sym] = Sma(self.fast)
            self._slow_sma[sym] = Sma(self.slow)

        fast_val = self._fast_sma[sym].update(bar.close)
        slow_val = self._slow_sma[sym].update(bar.close)

        if fast_val is None or slow_val is None:
            return

        count = self._bar_count[sym] = self._bar_count.get(sym, 0) + 1
        position = self.position(bar.instrument_id)

        # Signal per instrument
        if fast_val > slow_val and position <= 0:
            self._trade_to(bar.instrument_id, bar.close, position)
        elif fast_val < slow_val and position > 0:
            self.sell(bar.instrument_id, position)
        elif position > 0 and count % self.rebalance_every == 0:
            self._trade_to(bar.instrument_id, bar.close, position)  # rebalance this member

    def _equity(self) -> float:
        held = sum(self.position(self._iids[s]) * px for s, px in self._last_close.items())
        return self._cash + held

    def _trade_to(self, iid, price: float, position: float) -> None:
        """Buy or sell the difference between ``position`` and the equal-weight target."""
        target = int(self._equity() / len(self.symbols) / price)
        delta = target - position
        if delta > 0:
            qty = min(int(delta), int(max(self._cash, 0.0) / price))
            if qty > 0:
                self.buy(iid, qty)
        elif delta < 0:
            self.sell(iid, -delta)


def run(
    bars: int = 100,
    fast: int = 10,
    slow: int = 30,
    rebalance_every: int = 20,
) -> dict[str, Any]:
    symbols = _DEFAULT_SYMBOLS
    bars_list = basket_bars(bars, symbols)

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
