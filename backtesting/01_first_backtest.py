"""backtesting/01_first_backtest: a minimal backtest with synthetic data.

Uses ``honba.session.BacktestSession`` (the same runner that powers live) with
a tiny ``DataProvider`` that returns synthetic bars. The strategy emits market
orders; here ``replay(..., fill_delay=1)`` fills them at the next bar's open, the same timing as
Honba's ``next_open`` model for daily sessions (``honba.backtest.simulated.NextOpenExecution``,
which adds costs and settlement; ``honba_examples.backtest.run_portfolio_backtest`` wraps it for
multi-instrument runs). The report names its data source (``lakehouse`` or ``synthetic``).

Run::

    python backtesting/01_first_backtest.py
    python backtesting/01_first_backtest.py --synthetic --capital 50000
    python backtesting/01_first_backtest.py --out first_backtest.json

Pitfalls::

    - Fill timing is a simplification: replay's fill_delay=1 fills at the next bar's open with no
      gap, volume or queue model, so a large order or an overnight gap fills worse live. Orders
      from the last bar never fill.
    - Data differs by machine: without --synthetic the last --bars real bars from the local
      lakehouse are used when it holds enough, otherwise a straight-line ramp. The output now names
      the source, but numbers still differ across machines and ramp results say nothing about
      markets.
    - No friction: replay charges no STT, brokerage or slippage, and --capital goes all in on whole
      shares with no cash reserve. A profitable run here is gross of everything 02_cost_modeling
      shows.
    - Tiny sample: 60 bars with a 30-bar warm-up leave about 30 tradable bars and a couple of fills,
      all in one regime. Two trades cannot show an edge.
    - Defence shown: on_bar returns while either Sma is still None, so no signal is ever built from
      a partial window.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma

from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.jsonable import jsonable

__all__ = ["main", "run"]


class SmaBacktest(Strategy):
    """Fast SMA crossing above/below slow SMA → signal-driven fills."""

    name = "sma_backtest"
    warmup_bars = 30

    def __init__(self, fast: int = 10, slow: int = 30, capital: float = 100_000.0) -> None:
        super().__init__()
        self.fast = Sma(fast)
        self.slow = Sma(slow)
        self.capital = capital

    def on_bar(self, bar) -> None:
        fast_val = self.fast.update(bar.close)
        slow_val = self.slow.update(bar.close)

        if fast_val is None or slow_val is None:
            return

        position = self.position(bar.instrument_id)
        if fast_val > slow_val and position <= 0:
            qty = int(self.capital // bar.close)  # all-in, whole shares, sized off the signal close
            if qty > 0:
                self.buy(bar.instrument_id, qty)
        elif fast_val < slow_val and position > 0:
            self.sell(bar.instrument_id, position)


def run(
    bars: int = 60,
    fast: int = 10,
    slow: int = 30,
    capital: float = 100_000.0,
    symbol: str = "RELIANCE",
    synthetic: bool = False,
) -> dict[str, Any]:
    from honba_examples.synthetic import get_bars_with_source, trending_bars

    if synthetic:
        bars_list, data_source = trending_bars(symbol, bars), "synthetic"
    else:
        bars_list, data_source = get_bars_with_source(symbol, count=bars)

    strat = SmaBacktest(fast=fast, slow=slow, capital=capital)

    # Note: BacktestSession requires a real DataProvider; for this learning-path example
    # we use the honba.strategies.testing.replay harness instead. fill_delay=1 fills each order at
    # the next bar's open (the default 0 would fill at the signal bar's own close).
    from honba.strategies.testing import replay

    result = replay(strat, bars_list, fill_delay=1)

    # Net result from the fills: cash flows plus the open position marked at the last close
    cash = capital
    held = 0.0
    for f in result.fills:
        sign = 1.0 if f.side.value == "buy" else -1.0
        cash -= sign * f.quantity * f.price
        held += sign * f.quantity
    final_equity = cash + held * bars_list[-1].close if bars_list else capital

    strategy_repr = {
        "name": strat.name,
        "warmup_bars": strat.warmup_bars,
        "params": {"fast": fast, "slow": slow},
    }

    return {
        "strategy": strategy_repr,
        "config": {
            "bars": bars,
            "fast": fast,
            "slow": slow,
            "capital": capital,
            "data_source": data_source,
        },
        "result": jsonable(result),
        "summary": {
            "fills": len(result.fills),
            "intents": len(result.intents),
            "data_source": data_source,
            "final_equity": final_equity,
            "net_pnl": final_equity - capital,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--bars", type=int, default=60)
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument(
        "--initial-capital",
        "--capital",
        dest="capital",
        type=float,
        default=100_000.0,
        help="Initial capital in INR (default: 100000.0)",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="always use the synthetic ramp, even if the lakehouse holds real bars",
    )
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(
        bars=args.bars,
        fast=args.fast,
        slow=args.slow,
        capital=args.capital,
        synthetic=args.synthetic,
    )

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
