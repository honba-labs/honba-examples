"""strategies/15_multi_symbol_portfolio: 5-scrip NIFTY portfolio with per-symbol sizing and risk caps.

Demonstrates:
- Managing positions across 5 NIFTY scrips simultaneously
- Per-symbol allocation sizing with strict max-weight caps (e.g. 20% per scrip)
- Per-symbol stop-loss / loss-limit risk controls
- Scheduled portfolio-wide rebalancing to target weights

Run::

    python examples/strategies/15_multi_symbol_portfolio.py
    python examples/strategies/15_multi_symbol_portfolio.py --max-weight 0.25 --rebalance-every 10

Pitfalls::

    - Correlation spikes in tail regimes: holding 5 blue-chip NIFTY scrips provides diversification
      during normal regimes, but during macro panics cross-asset correlations approach 1.0, triggering
      simultaneous drawdowns across all constituents.
    - Staggered execution and liquidity drag: rebalancing 5 distinct symbols at the close assumes instantaneous
      parallel fills; in live markets, varying order book depth leads to staggered execution and leg slippage.
    - Cash drag from signal divergence: when multiple symbols exit on bearish SMA crosses, capital sits idle
      as uninvested cash unless a dynamic cash-management or treasury sweep overlay is implemented.
    - Survivorship bias in universe selection: selecting RELIANCE, TCS, INFY, HDFCBANK, and ICICIBANK with
      hindsight introduces survivorship bias; a point-in-time universe lookup is mandatory for institutional validity.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from typing import Any

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderSide
from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma

from honba_examples.cli import run_example
from honba_examples.replay_report import replay_report
from honba_examples.synthetic import bar
from honba_examples.synthetic import weekdays as weekdays_func

__all__ = ["MultiSymbolPortfolio", "five_scrip_bars", "main", "run"]

DEFAULT_5_SYMBOLS = ("RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK")
_DEFAULT_STEPS = (0.5, 0.3, 1.2, 0.8, 0.6)


def five_scrip_bars(
    bars: int,
    symbols: tuple[str, ...] = DEFAULT_5_SYMBOLS,
    start_date: dt.date = dt.date(2026, 1, 1),
) -> list[Bar]:
    """Generate daily bars across 5 scrips with differing price paths, sorted chronologically."""
    out: list[Bar] = []
    days = weekdays_func(start_date, bars)
    for i, d in enumerate(days):
        for k, sym in enumerate(symbols):
            step = _DEFAULT_STEPS[k % len(_DEFAULT_STEPS)]
            price = 2500.0 + i * step
            out.append(bar(sym, d, price))
    out.sort(key=lambda b: (b.ts, b.instrument_id.symbol))
    return out


class MultiSymbolPortfolio(Strategy):
    """5-scrip portfolio strategy with per-symbol sizing and risk caps."""

    name = "multi_symbol_portfolio"
    warmup_bars = 30

    def __init__(
        self,
        symbols: tuple[str, ...] = DEFAULT_5_SYMBOLS,
        fast: int = 10,
        slow: int = 30,
        rebalance_every: int = 20,
        max_weight: float = 0.20,
        symbol_max_loss_pct: float = 0.05,
        capital: float = 100_000.0,
    ) -> None:
        super().__init__()
        self.symbols = symbols
        self.fast = fast
        self.slow = slow
        self.rebalance_every = rebalance_every
        self.max_weight = max_weight
        self.symbol_max_loss_pct = symbol_max_loss_pct
        self.capital = capital
        self._cash = capital

        self._fast_sma: dict[str, Sma] = {}
        self._slow_sma: dict[str, Sma] = {}
        self._bar_count: dict[str, int] = {}
        self._iids: dict[str, InstrumentId] = {}
        self._last_close: dict[str, float] = {}
        self._entry_price: dict[str, float] = {}

    def on_fill(self, fill) -> None:
        sign = -1.0 if fill.side is OrderSide.BUY else 1.0
        self._cash += sign * fill.quantity * fill.price
        sym = fill.instrument_id.symbol
        if fill.side is OrderSide.BUY:
            if sym not in self._entry_price or self.position(fill.instrument_id) <= 0:
                self._entry_price[sym] = fill.price
        else:
            if self.position(fill.instrument_id) <= 0:
                self._entry_price.pop(sym, None)

    def on_bar(self, bar: Bar) -> None:
        sym = bar.instrument_id.symbol
        self._iids[sym] = bar.instrument_id
        self._last_close[sym] = bar.close

        # Initialize indicators per symbol
        if sym not in self._fast_sma:
            self._fast_sma[sym] = Sma(self.fast)
            self._slow_sma[sym] = Sma(self.slow)

        fast_val = self._fast_sma[sym].update(bar.close)
        slow_val = self._slow_sma[sym].update(bar.close)

        if fast_val is None or slow_val is None:
            return

        count = self._bar_count[sym] = self._bar_count.get(sym, 0) + 1
        pos = self.position(bar.instrument_id)

        # 1. Per-symbol loss cap check
        if pos > 0 and sym in self._entry_price:
            loss_pct = (self._entry_price[sym] - bar.close) / self._entry_price[sym]
            if loss_pct >= self.symbol_max_loss_pct:
                self.sell(bar.instrument_id, pos)
                return

        # 2. SMA trend signals with per-symbol max-weight cap
        if fast_val > slow_val and pos <= 0:
            self._allocate_symbol(bar.instrument_id, bar.close, pos)
        elif fast_val < slow_val and pos > 0:
            self.sell(bar.instrument_id, pos)
        elif pos > 0 and count % self.rebalance_every == 0:
            # Rebalance member back to capped target
            self._allocate_symbol(bar.instrument_id, bar.close, pos)

    def _portfolio_equity(self) -> float:
        held_value = sum(
            self.position(self._iids[s]) * px
            for s, px in self._last_close.items()
            if s in self._iids
        )
        return self._cash + held_value

    def _allocate_symbol(self, iid: InstrumentId, price: float, current_pos: float) -> None:
        """Size position respecting the per-symbol max weight cap."""
        equity = self._portfolio_equity()
        if equity <= 0 or price <= 0:
            return

        # Target weight is equal weight, capped by max_weight
        eq_weight = 1.0 / len(self.symbols)
        target_weight = min(eq_weight, self.max_weight)
        target_notional = equity * target_weight
        target_qty = int(target_notional / price)

        delta = target_qty - current_pos
        if delta > 0:
            # Check cash availability
            affordable = int(max(0.0, self._cash) / price)
            qty = min(int(delta), affordable)
            if qty > 0:
                self.buy(iid, float(qty))
        elif delta < 0:
            self.sell(iid, float(-delta))


def run(
    bars: int = 100,
    fast: int = 10,
    slow: int = 30,
    rebalance_every: int = 20,
    max_weight: float = 0.20,
    symbol_max_loss_pct: float = 0.05,
    symbols: tuple[str, ...] = DEFAULT_5_SYMBOLS,
) -> dict[str, Any]:
    bars_list = five_scrip_bars(bars, symbols=symbols)
    strat = MultiSymbolPortfolio(
        symbols=symbols,
        fast=fast,
        slow=slow,
        rebalance_every=rebalance_every,
        max_weight=max_weight,
        symbol_max_loss_pct=symbol_max_loss_pct,
    )
    return replay_report(
        strat,
        bars_list,
        {
            "fast": fast,
            "slow": slow,
            "rebalance_every": rebalance_every,
            "max_weight": max_weight,
            "symbol_max_loss_pct": symbol_max_loss_pct,
            "symbols": list(symbols),
        },
        config_extra={"bars": bars},
    )


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bars", type=int, default=100, help="number of synthetic bars")
    parser.add_argument("--fast", type=int, default=10, help="fast SMA period")
    parser.add_argument("--slow", type=int, default=30, help="slow SMA period")
    parser.add_argument(
        "--rebalance-every", type=int, default=20, help="rebalance interval in bars"
    )
    parser.add_argument(
        "--max-weight", type=float, default=0.20, help="max portfolio weight per symbol"
    )
    parser.add_argument(
        "--symbol-max-loss",
        type=float,
        default=0.05,
        dest="symbol_max_loss_pct",
        help="max loss pct per symbol before stop-out",
    )


def main(argv: list[str] | None = None) -> int:
    return run_example(run, __doc__, _add_args, argv=argv)


if __name__ == "__main__":
    sys.exit(main())
