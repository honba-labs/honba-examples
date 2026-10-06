"""backtesting/06_concurrent_backtest: parameter sweep with concurrent execution.

Uses ``honba.strategies.testing.replay`` (bar-close fills, no settlement or costs)
in a thread pool to evaluate a grid of (fast, slow)
periods. Reports the Pareto frontier (return vs drawdown) and the best by
Sharpe.

Run::

    python backtesting/06_concurrent_backtest.py --workers 4
"""

from __future__ import annotations

import argparse
import concurrent.futures
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
from honba_examples.metrics import curve_metrics
from tests.synthetic import bar as synth_bar, weekdays as weekdays_func

__all__ = ["run", "main"]


class SmaSweep(Strategy):
    """SMA crossover for parameter sweeps."""

    name: str = "sma_sweep"
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


def _backtest(bars_list: list, fast: int, slow: int) -> dict[str, Any]:
    strat = SmaSweep(fast=fast, slow=slow)
    result = replay(strat, bars_list)

    if len(result.fills) < 2:
        return {"fast": fast, "slow": slow, "sharpe": 0.0, "return_pct": 0.0, "max_dd_pct": 0.0}

    # Build equity curve from fills
    cash = 100_000.0
    position = 0.0
    curve = [{"value": cash, "cash": cash, "date": str(bars_list[0].ts)}]
    for fill in result.fills:
        notional = fill.quantity * fill.price
        if fill.side.value == "buy":
            cash -= notional
            position += fill.quantity
        else:
            cash += notional
            position -= fill.quantity
        equity = cash + position * fill.price
        curve.append({"value": equity, "cash": cash, "date": str(fill.ts)})

    # Close any open position at last price
    if position > 0:
        last_price = result.fills[-1].price
        cash += position * last_price
        curve.append({"value": cash, "cash": cash, "date": str(result.fills[-1].ts + 1)})

    # Format for curve_metrics
    from datetime import datetime, timezone
    metric_curve = [
        {
            "date": datetime.fromtimestamp(int(c["date"]) / 1e9, tz=timezone.utc).date().isoformat(),
            "value": c["value"],
            "cash": c["cash"]
        }
        for c in curve
    ]

    metrics = curve_metrics(metric_curve, 100_000.0)
    return {
        "fast": fast,
        "slow": slow,
        "fills": len(result.fills),
        "final_equity": metrics["final_value"],
        "return_pct": metrics["total_return_pct"],
        "max_dd_pct": metrics["max_drawdown_pct"],
        "sharpe": metrics["sharpe"],
    }


def run(
    bars: int = 120,
    fast_range: tuple[int, int] = (5, 20),
    slow_range: tuple[int, int] = (20, 50),
    workers: int = 4,
) -> dict[str, Any]:
    start = dt.date(2026, 1, 1)
    bars_list = [synth_bar("RELIANCE", d, 2500.0 + i * 0.5) for i, d in enumerate(weekdays_func(start, bars))]

    # Generate parameter grid
    params = [(f, s) for f in range(fast_range[0], fast_range[1] + 1, 2)
              for s in range(slow_range[0], slow_range[1] + 1, 5)
              if f < s]

    # Concurrent execution
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        # Collect in grid order, not completion order: thread scheduling must not
        # decide the order of equal-scoring results.
        futures = [executor.submit(_backtest, bars_list, f, s) for f, s in params]
        results = [future.result() for future in futures]

    # Find Pareto frontier (max return for given max DD, or max Sharpe)
    results.sort(key=lambda r: (-r["sharpe"], r["fast"], r["slow"]))
    best_sharpe = results[0] if results else None

    # Pareto: max return for each drawdown bucket
    results.sort(key=lambda r: (r["max_dd_pct"], r["fast"], r["slow"]))
    pareto = []
    best_ret = -float("inf")
    for r in results:
        if r["return_pct"] > best_ret:
            pareto.append(r)
            best_ret = r["return_pct"]

    return {
        "config": {"bars": bars, "fast_range": fast_range, "slow_range": slow_range, "workers": workers},
        "total_combinations": len(params),
        "best_by_sharpe": best_sharpe,
        "pareto_frontier": pareto,
        "all": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bars", type=int, default=120)
    parser.add_argument("--fast-min", type=int, default=5)
    parser.add_argument("--fast-max", type=int, default=20)
    parser.add_argument("--slow-min", type=int, default=20)
    parser.add_argument("--slow-max", type=int, default=50)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(
        bars=args.bars,
        fast_range=(args.fast_min, args.fast_max),
        slow_range=(args.slow_min, args.slow_max),
        workers=args.workers,
    )

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())