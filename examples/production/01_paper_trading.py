"""production/01_paper_trading: Paper trading session with simulated fills.

Strategy: inline `SmaPaper`, a 10/30 SMA crossover (`--strategy sma_crossover`).
Replays it on synthetic bars with next-open fills and reports equity marked to each
bar close. The risk-limit arguments are echoed in the config but not enforced. Teaches
the paper-trading loop before live capital; runs are offline and deterministic.

Run::

    python production/01_paper_trading.py --strategy sma_crossover --capital 100000

Pitfalls::

    - Paper is not live: fills are the replay engine's next-bar open on synthetic bars. There is no
      spread, queue position, partial fill, rejection, latency beyond one bar, brokerage or STT, and
      a buy is never refused for lack of cash. The synthetic series rises 0.5 a bar with close equal
      to open, so it crosses once and holds; its P&L says nothing about the strategy.
    - Risk guards are not enforced: --max-position-pct and --max-daily-loss are echoed in config
      only, and the order size is a fixed 1 share. Wire real limits (and a kill switch that flattens
      and stops the loop) before treating a paper run as a rehearsal of live risk control.
    - Equity is marked at each bar close for one symbol, so drawdown is a daily-close number and
      misses intraday moves. Bars are dated weekdays from 2026-01-01 with no IST session times,
      exchange holidays or market-hours guard, which a live loop needs.
    - Idempotency and restarts: replay never re-sends an order, but a live loop that restarts and
      replays its last bar can double-submit unless every order carries a client_order_id and the
      loop reconciles with the broker's order book first.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any

from honba.domain.instrument import InstrumentId
from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma

from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.metrics import fills_equity_curve
from honba_examples.synthetic import bar as synth_bar
from honba_examples.synthetic import weekdays as weekdays_func

__all__ = ["main", "run"]


class SmaPaper(Strategy):
    """SMA crossover for paper trading."""

    name = "sma_paper"
    warmup_bars = 30

    def __init__(self, fast: int = 10, slow: int = 30, symbol: str = "RELIANCE") -> None:
        super().__init__()
        self.fast = Sma(fast)
        self.slow = Sma(slow)
        self.symbol = symbol
        self.iid = InstrumentId(symbol=symbol, exchange="NSE")

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
    bars_list = [
        synth_bar(symbol, d, 2500.0 + i * 0.5) for i, d in enumerate(weekdays_func(start, bars))
    ]

    # Paper trading uses fake adapter with simulated fills
    from honba.strategies.testing import replay

    strat = SmaPaper(fast=fast, slow=slow, symbol=symbol)
    result = replay(strat, bars_list, fill_delay=1)  # next-open fills

    # Compute metrics
    fills = result.fills
    intents = result.intents

    # Equity marked to each bar close, so an open position carries its unrealised P&L
    equity_curve = fills_equity_curve(capital, fills, bars_list)

    final_equity = equity_curve[-1] if equity_curve else capital
    total_return = final_equity - capital
    return_pct = (total_return / capital) * 100

    # Max drawdown
    peak = capital
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        dd = (peak - eq) / peak * 100
        max_dd = max(max_dd, dd)

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
    parser = make_parser(__doc__)
    parser.add_argument("--strategy", type=str, default="sma_crossover")
    parser.add_argument(
        "--initial-capital",
        "--capital",
        dest="capital",
        type=float,
        default=100_000.0,
        help="Initial capital in INR (default: 100000.0)",
    )
    parser.add_argument("--bars", type=int, default=100)
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--symbol", type=str, default="RELIANCE")
    parser.add_argument("--max-position-pct", type=float, default=0.1)
    parser.add_argument("--max-daily-loss", type=float, default=50_000.0)
    add_out_arg(parser)
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

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
