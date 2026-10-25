"""strategies/15_multi_symbol_portfolio: multi-symbol portfolio management across 5 NIFTY scrips.

Demonstrates:
- Managing a 5-scrip portfolio book (RELIANCE, TCS, INFY, HDFCBANK, ICICIBANK) simultaneously
- Scheduled periodic portfolio rebalancing across instruments
- Strict per-symbol exposure limits (max weight cap: default 20% per scrip)
- Independent per-symbol drawdown risk guard and stop-out triggers

Run::

    python examples/strategies/15_multi_symbol_portfolio.py
    python examples/strategies/15_multi_symbol_portfolio.py --max-weight 0.15 --rebalance-every 10

Pitfalls::

    - Cross-asset liquidity imbalances: when one symbol in the basket experiences a volume dry-up,
      equal-weight rebalancing can stall or incur outsized impact cost relative to more liquid names.
    - Synchronous bar assumption: real multi-symbol streaming feeds produce interleaved ticks at variable
      rates; assuming aligned simultaneous bar events creates artificial lead-lag arbitrage in backtests.
    - Cash drag and rounding: enforcing strict percentage weight caps on small retail account balances
      causes significant cash drag due to non-fractional integer share lot constraints on NSE.
    - Correlation breakdown during drawdowns: during systemic market sell-offs, cross-sectional asset
      correlations spike toward 1.0, nullifying single-stock diversification benefits unless beta-hedged.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from typing import ClassVar, override

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderSide
from honba.domain.trade import Trade
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
    start_date: dt.date | None = None,
) -> list[Bar]:
    """Generate daily bars across 5 scrips with differing price paths, sorted chronologically."""
    out: list[Bar] = []
    start = start_date if start_date is not None else dt.date(2026, 1, 1)
    days = weekdays_func(start, bars)
    for i, d in enumerate(days):
        for k, sym in enumerate(symbols):
            step = _DEFAULT_STEPS[k % len(_DEFAULT_STEPS)]
            price = 2500.0 + i * step
            out.append(bar(sym, d, price))
    out.sort(key=lambda b: (b.ts, b.instrument_id.symbol))
    return out


class MultiSymbolPortfolio(Strategy):
    """5-scrip portfolio strategy with per-symbol sizing and risk caps."""

    name: ClassVar[str] = "multi_symbol_portfolio"
    warmup_bars: ClassVar[int] = 30
    symbols: tuple[str, ...]
    fast: int
    slow: int
    rebalance_every: int
    max_weight: float
    symbol_max_loss_pct: float
    capital: float
    _cash: float
    _fast_sma: dict[str, Sma]
    _slow_sma: dict[str, Sma]
    _bar_count: dict[str, int]
    _iids: dict[str, InstrumentId]
    _last_close: dict[str, float]
    _entry_price: dict[str, float]

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

        self._fast_sma = {}
        self._slow_sma = {}
        self._bar_count = {}
        self._iids = {}
        self._last_close = {}
        self._entry_price = {}

    @override
    def on_fill(self, fill: Trade) -> None:
        sign = -1.0 if fill.side is OrderSide.BUY else 1.0
        self._cash += sign * fill.quantity * fill.price
        sym = fill.instrument_id.symbol
        if fill.side is OrderSide.BUY:
            if sym not in self._entry_price or self.position(fill.instrument_id) <= 0:
                self._entry_price[sym] = fill.price
        else:
            if self.position(fill.instrument_id) <= 0:
                _ = self._entry_price.pop(sym, None)

    @override
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

        # 3. Scheduled periodic rebalancing
        if count % self.rebalance_every == 0 and pos > 0:
            self._rebalance_symbol(bar.instrument_id, bar.close, pos)

    def _estimate_portfolio_value(self) -> float:
        total = self._cash
        for s, iid in self._iids.items():
            qty = self.position(iid)
            price = self._last_close.get(s, 0.0)
            total += qty * price
        return max(total, self.capital)

    def _allocate_symbol(self, iid: InstrumentId, price: float, current_pos: float) -> None:
        pv = self._estimate_portfolio_value()
        target_val = pv * self.max_weight
        current_val = current_pos * price
        needed_val = target_val - current_val

        if needed_val > 0 and self._cash > 0:
            spend = min(needed_val, self._cash * 0.95)
            qty = int(spend / price) if price > 0 else 0
            if qty > 0:
                self.buy(iid, float(qty))

    def _rebalance_symbol(self, iid: InstrumentId, price: float, current_pos: float) -> None:
        pv = self._estimate_portfolio_value()
        target_val = pv * self.max_weight
        current_val = current_pos * price
        diff_val = target_val - current_val

        # If deviated by more than 5% of target allocation, rebalance
        if abs(diff_val) > target_val * 0.05 and price > 0:
            delta_qty = int(abs(diff_val) / price)
            if diff_val < 0 and delta_qty > 0:
                sell_qty = min(current_pos, float(delta_qty))
                self.sell(iid, sell_qty)
            elif diff_val > 0 and delta_qty > 0 and self._cash >= delta_qty * price:
                self.buy(iid, float(delta_qty))


def run(
    bars: int = 100,
    fast: int = 10,
    slow: int = 30,
    rebalance_every: int = 20,
    max_weight: float = 0.20,
    symbol_max_loss_pct: float = 0.05,
    symbols: tuple[str, ...] = DEFAULT_5_SYMBOLS,
) -> dict[str, object]:
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
            "symbols": symbols,
        },
        config_extra={"bars": len(bars_list)},
    )


def _add_args(parser: argparse.ArgumentParser) -> None:
    _ = parser.add_argument("--bars", type=int, default=100, help="number of synthetic bars")
    _ = parser.add_argument("--fast", type=int, default=10, help="fast SMA period")
    _ = parser.add_argument("--slow", type=int, default=30, help="slow SMA period")
    _ = parser.add_argument(
        "--rebalance-every", type=int, default=20, help="rebalance interval in bars"
    )
    _ = parser.add_argument(
        "--max-weight", type=float, default=0.20, help="max portfolio weight per symbol"
    )
    _ = parser.add_argument(
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
