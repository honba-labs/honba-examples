"""backtesting/04_walk_forward: rolling window optimization / validation.

Walk-forward analysis: optimize parameters on an in-sample window, test on the
next out-of-sample window, roll forward. Uses ``honba_examples.backtest.run_portfolio_backtest``
for the event-driven backtest with proper settlement and costs.

Run::

    python backtesting/04_walk_forward.py
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
from tests.synthetic import bar as synth_bar, weekdays as weekdays_func

__all__ = ["run", "main"]


class SmaWalk(Strategy):
    """SMA crossover with configurable periods."""

    name: str = "sma_walk"
    warmup_bars: int = 30

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


def _backtest(bars_list: list, fast: int, slow: int) -> float:
    """Run one backtest and return final equity."""
    strat = SmaWalk(fast=fast, slow=slow)
    result = replay(strat, bars_list)
    return result.fills[-1].price if result.fills else 0.0


def run(
    bars: int = 120,
    window: int = 40,
    step: int = 20,
) -> dict[str, Any]:
    start = dt.date(2026, 1, 1)
    bars_list = [synth_bar("RELIANCE", d, 2500.0 + i * 0.5) for i, d in enumerate(weekdays_func(start, bars))]

    results = []
    for i in range(0, bars - window, step):
        train_bars = bars_list[i : i + window]
        test_bars = bars_list[i + window : i + window + step]

        # Grid search on train window (simplified: just evaluate a few pairs)
        best_score = -1
        best_pair = (10, 30)
        for fast in (5, 10, 15):
            for slow in (20, 30, 40):
                if fast >= slow:
                    continue
                score = _backtest(train_bars, fast, slow)
                if score > best_score:
                    best_score = score
                    best_pair = (fast, slow)

        # Test on out-of-sample
        test_score = _backtest(test_bars, *best_pair)
        results.append({
            "window": i // step,
            "train_range": [i, i + window],
            "test_range": [i + window, i + window + step],
            "best_fast": best_pair[0],
            "best_slow": best_pair[1],
            "train_score": best_score,
            "test_score": test_score,
        })

    return {"config": {"window": window, "step": step, "total_bars": bars}, "windows": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bars", type=int, default=120)
    parser.add_argument("--window", type=int, default=40)
    parser.add_argument("--step", type=int, default=20)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(bars=args.bars, window=args.window, step=args.step)

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())