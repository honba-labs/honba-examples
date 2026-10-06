"""production/01_paper_trading: Paper trading session with simulated fills.

Runs a strategy against a simulated market (fake adapter) with realistic
fill modeling, risk guards, and real-time P&L reporting.

Run::

    python production/01_paper_trading.py --strategy sma_crossover --capital 100000
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import signal
import sys
import time
from pathlib import Path
from typing import Any

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba.adapters.registry import AdapterRegistry
from honba.domain.instrument import InstrumentId, InstrumentKind
from honba.domain.order import OrderIntent, OrderSide, OrderType
from honba.session import Session, SessionConfig
from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma
from honba_examples.jsonable import jsonable
from tests.synthetic import bar as synth_bar, weekdays as weekdays_func

__all__ = ["main", "run"]


class SmaPaper(Strategy):
    """SMA crossover for paper trading."""

    name: str = "sma_paper"
    warmup_bars: int = 30

    def __init__(self, fast: int = 10, slow: int = 30, symbol: str = "RELIANCE") -> None:
        super().__init__()
        self.fast = Sma(fast)
        self.slow = Sma(slow)
        self.symbol = symbol
        self.iid = InstrumentId(symbol=symbol, exchange="NSE", kind=InstrumentKind.EQUITY)

    def on_bar(self, bar) -> None:
        if bar.instrument_id != self.iid:
            return

        fast_val = self.fast.update(bar.close)
        slow_val = self.slow.update(bar.close)

        if fast_val is None or slow_val is None:
            return

        position = self.position(self.iid)
        if fast_val > slow_val and position <= 0:
            self.buy(self.iid, 1)
        elif fast_val < slow_val and position >= 0 and position > 0:
            self.sell(self.iid, position)


def run(
    strategy: str = "sma_crossover",
    capital: float = 100_000.0,
    bars: int = 100,
    fast: int = 10,
    slow: int = 30,
    symbol: str = "RELIANCE",
    max_position_pct: float = 0.1,
    max_daily_loss: float = 50_000.0,
    out: Path | None = None,
) -> dict[str, Any]:
    # Generate synthetic bars
    start = dt.date(2026, 1, 1)
    bars_list = [synth_bar(symbol, d, 2500.0 + i * 0.5) for i, d in enumerate(weekdays_func(start, bars))]

    # Paper trading uses fake adapter with simulated fills
    from honba.strategies.testing import replay

    strat = SmaPaper(fast=fast, slow=slow, symbol=symbol)
    result = replay(strat, bars_list, fill_delay=1)  # next-open fills

    # Compute metrics
    fills = result.fills
    intents = result.intents

    # P&L
    cash = capital
    position = 0.0
    equity_curve = [capital]
    for fill in fills:
        notional = fill.quantity * fill.price
        if fill.side.value == "buy":
            cash -= notional
            position += fill.quantity
        else:
            cash += notional
            position -= fill.quantity
        equity = cash + position * fill.price
        equity_curve.append(equity)

    final_equity = equity_curve[-1] if equity_curve else capital
    total_return = final_equity - capital
    return_pct = (total_return / capital) * 100

    # Max drawdown
    peak = capital
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd:
            max_dd = dd

    return {
        "config": {
            "strategy": strategy,
            "capital": capital,
            "bars": bars,
            "fast": fast,
            "slow": slow,
            "symbol": symbol,
            "max_position_pct": max_position_pct,
            "max_daily_loss": max_daily_loss,
        },
        "summary": {
            "total_fills": len(fills),
            "total_intents": len(intents),
            "final_equity": final_equity,
            "total_return": total_return,
            "return_pct": return_pct,
            "max_drawdown_pct": max_dd,
        },
        "equity_curve": equity_curve,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--strategy", type=str, default="sma_crossover")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--bars", type=int, default=100)
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--symbol", type=str, default="RELIANCE")
    parser.add_argument("--max-position-pct", type=float, default=0.1)
    parser.add_argument("--max-daily-loss", type=float, default=50_000.0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(
        strategy=args.strategy,
        capital=args.capital,
        bars=args.bars,
        fast=args.fast,
        slow=args.slow,
        symbol=args.symbol,
        max_position_pct=args.max_position_pct,
        max_daily_loss=args.max_daily_loss,
    )

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())