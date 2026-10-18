"""backtesting/04_walk_forward: rolling window optimization / validation.

Walk-forward analysis: optimize parameters on an in-sample window, test on the
next out-of-sample window, roll forward. Uses ``honba.strategies.testing.replay`` (bar-close fills, no settlement or costs)
on deterministic sine-wave bars; each window reports the net return of the best in-sample pair
in and out of sample. See ``honba_examples.backtest.run_portfolio_backtest`` for next-open fills with both.

Run::

    python examples/backtesting/04_walk_forward.py

Pitfalls::

    - The score is a one-position, all-in net return on 100000 (fills plus the open position marked
      at the last close), gross of costs. It is not risk-adjusted: pick by drawdown or Sharpe too,
      and note a slice that ends in a position is judged on an unrealised mark.
    - Test slices must outlast the warm-up: a fresh strategy needs `slow` bars before it can
      signal, so an out-of-sample slice shorter than that validates nothing. The defaults use 60-bar
      slices against a 40-bar maximum warm-up, and config.warnings flags a step that is too short.
      Even then a slice holds one or two trades, so test scores are noisy.
    - Defence shown: parameters are chosen on bars_list[i : i + window] only and scored on the
      following step bars, with no overlap, then the window rolls forward by step. Keep that
      separation; honba.algo_analytics.walk_forward adds the pass/fail gates.
    - Every grid point is a trial: 3 fast x 3 slow values are searched per window. The larger the
      grid and the shorter the window, the more likely the in-sample winner is noise, so log how
      many variants were tried and prefer a flat plateau to a single peak.
    - replay fills at the signal bar's close and charges no costs, and the data is a synthetic sine,
      so even a clean walk-forward here is a demonstration of structure, not of performance.
"""

from __future__ import annotations

import datetime as dt
import sys
from typing import Any

from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma
from honba.strategies.testing import replay

from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.synthetic import oscillating_bars

__all__ = ["main", "run"]

CAPITAL = 100_000.0
GRID_FAST = (5, 10, 15)
GRID_SLOW = (20, 30, 40)


class SmaWalk(Strategy):
    """SMA crossover with configurable periods."""

    name = "sma_walk"
    warmup_bars = 30

    def __init__(self, fast: int = 10, slow: int = 30, capital: float = CAPITAL) -> None:
        super().__init__()
        self.capital = capital
        self.fast = Sma(fast)
        self.slow = Sma(slow)

    def on_bar(self, bar) -> None:
        fast_val = self.fast.update(bar.close)
        slow_val = self.slow.update(bar.close)

        if fast_val is None or slow_val is None:
            return

        position = self.position(bar.instrument_id)
        if fast_val > slow_val and position <= 0:
            qty = int(self.capital // bar.close)
            if qty > 0:
                self.buy(bar.instrument_id, qty)
        elif fast_val < slow_val and position > 0:
            self.sell(bar.instrument_id, position)


def _evaluate(bars_list: list, fast: int, slow: int) -> tuple[float, int]:
    """Net return on ``CAPITAL`` (fills plus the open position at the last close) and buy count."""
    result = replay(SmaWalk(fast=fast, slow=slow), bars_list)
    cash, held = CAPITAL, 0.0
    for f in result.fills:
        sign = 1.0 if f.side.value == "buy" else -1.0
        cash -= sign * f.quantity * f.price
        held += sign * f.quantity
    equity = cash + held * bars_list[-1].close if bars_list else CAPITAL
    return equity / CAPITAL - 1.0, sum(1 for f in result.fills if f.side.value == "buy")


def _backtest(bars_list: list, fast: int, slow: int) -> float:
    """Run one backtest and return the net return (0.01 = +1%)."""
    return _evaluate(bars_list, fast, slow)[0]


def run(
    bars: int = 400,
    window: int = 120,
    step: int = 60,
) -> dict[str, Any]:
    start = dt.date(2026, 1, 1)
    bars_list = oscillating_bars(
        "RELIANCE", bars, start=start, amplitude=100.0, period=50, drift=1.0
    )

    warmup = max(GRID_SLOW)
    warnings = []
    if step <= warmup:
        warnings.append(
            f"test slices of {step} bars are not longer than the {warmup}-bar warm-up: "
            "a pair with slow >= the slice length cannot form a signal out of sample, so such "
            "windows score 0.0 for lack of data, not for lack of edge"
        )

    results = []
    for i in range(0, bars - window - step + 1, step):
        train_bars = bars_list[i : i + window]
        test_bars = bars_list[i + window : i + window + step]

        # Grid search on train window (simplified: just evaluate a few pairs)
        best_score = float("-inf")
        best_pair = (10, 30)
        best_trades = 0
        for fast in GRID_FAST:
            for slow in GRID_SLOW:
                if fast >= slow:
                    continue
                score, trades = _evaluate(train_bars, fast, slow)
                if score > best_score:
                    best_score, best_pair, best_trades = score, (fast, slow), trades

        # Test on out-of-sample
        test_score, test_trades = _evaluate(test_bars, *best_pair)
        results.append(
            {
                "window": i // step,
                "train_range": [i, i + window],
                "test_range": [i + window, i + window + step],
                "best_fast": best_pair[0],
                "best_slow": best_pair[1],
                "train_score": best_score,
                "test_score": test_score,
                "train_trades": best_trades,
                "test_trades": test_trades,
            }
        )

    return {
        "config": {
            "window": window,
            "step": step,
            "total_bars": bars,
            "warmup_bars": warmup,
            "warnings": warnings,
        },
        "windows": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--bars", type=int, default=400)
    parser.add_argument("--window", type=int, default=120)
    parser.add_argument("--step", type=int, default=60)
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(bars=args.bars, window=args.window, step=args.step)

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
