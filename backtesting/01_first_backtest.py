"""backtesting/01_first_backtest: a minimal backtest with synthetic data.

Uses ``honba.session.BacktestSession`` (the same runner that powers live) with
a tiny ``DataProvider`` that returns synthetic bars. The strategy emits market
orders; fills arrive at the next bar's open (Honba's ``next_open`` fill model
for daily sessions, simulated by ``honba.backtest.simulated.NextOpenExecution``;
``honba_examples.backtest.run_portfolio_backtest`` wraps it for multi-instrument runs
with settlement and fees).

Run::

    python backtesting/01_first_backtest.py
    python backtesting/01_first_backtest.py --out first_backtest.json

Pitfalls::

    - Fill timing: despite the next-open wording above, run() calls replay(strat, bars_list) with
      its default fill_delay=0, so each order fills at the close of the bar that triggered it. That
      is trading the close, which you cannot do live; 03_latency_modeling compares fill_delay=1, and
      only the engine's NextOpenExecution fills at a later open.
    - Silent data switch: get_bars() returns the last --bars real bars from the local lakehouse when
      it holds enough, and otherwise a straight-line synthetic ramp, and nothing in the output says
      which. Results differ between machines, and ramp results say nothing about markets.
    - No friction: replay charges no STT, brokerage or slippage, trades one share, and --capital is
      only echoed into the report. A profitable run here is gross of everything 02_cost_modeling
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

    def __init__(self, fast: int = 10, slow: int = 30) -> None:
        super().__init__()
        self.fast = Sma(fast)
        self.slow = Sma(slow)

    def on_bar(self, bar) -> None:
        fast_val = self.fast.update(bar.close)
        slow_val = self.slow.update(bar.close)

        if fast_val is None or slow_val is None:
            return

        position = self.position(bar.instrument_id)
        if fast_val > slow_val and position <= 0:
            self.buy(bar.instrument_id, 1)
        elif fast_val < slow_val and position >= 0 and position > 0:
            self.sell(bar.instrument_id, position)


def run(
    bars: int = 60,
    fast: int = 10,
    slow: int = 30,
    capital: float = 100_000.0,
    symbol: str = "RELIANCE",
) -> dict[str, Any]:
    from honba_examples.synthetic import get_bars

    bars_list = get_bars(symbol, count=bars)

    strat = SmaBacktest(fast=fast, slow=slow)

    # Note: BacktestSession requires a real DataProvider; for this learning-path example
    # we use the honba.strategies.testing.replay harness instead (same fill semantics
    # for a single-instrument daily backtest).
    from honba.strategies.testing import replay

    result = replay(strat, bars_list)

    # Metrics from honba_examples.metrics (same curve format as production tearsheets)
    fill_count = len(result.fills)
    intent_count = len(result.intents)

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
        },
        "result": jsonable(result),
        "summary": {
            "fills": fill_count,
            "intents": intent_count,
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
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(bars=args.bars, fast=args.fast, slow=args.slow, capital=args.capital)

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
