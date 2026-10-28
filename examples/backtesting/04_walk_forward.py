"""backtesting/04_walk_forward: Walk-forward analysis with out-of-sample gates.

Upgraded to use ``honba.algo_analytics.walk_forward`` (rolling expanding/rolling
windows, full Honba backtest with next-open fills, NSE delivery costs, T+1
settlement) for real data. For synthetic data, uses the same fold geometry with
``honba.strategies.testing.replay`` for fast deterministic runs.
Requires Walk-Forward Efficiency (WFE) ≥ 0.60 as a reported gate
on real data; synthetic-data runs report WFE without asserting.

Run::

    python examples/backtesting/04_walk_forward.py --symbol RELIANCE \\
        --start 2020-01-01 --end 2024-12-31 \\
        --train-months 24 --test-months 6 --n-folds 8

Pitfalls::

    - The walk_forward function runs *two* full Honba.backtest sessions per fold
      (train window, then test window) with the caller's backtest settings.
      This is slow but correct: no shared state between folds.
    - Data range must cover ``train_months + n_folds * test_months`` from
      ``start``. The function raises if the range is insufficient.
    - WFE gate (mean OOS Sharpe / mean IS Sharpe ≥ 0.60) is reported on real
      data; synthetic runs compute it but do not assert. The gates also require
      OOS Sharpe std < 0.5 and ≥ 75% profitable folds.
    - ``window=expanding`` (default) anchors training at ``start``; ``rolling``
      slides the training window. Expanding uses more data but risks regime
      contamination; rolling adapts but has less training data per fold.
    - Each fold gets a fresh strategy instance (deep-copied for instances;
      classes/paths re-resolved). State from one fold's training *never* leaks
      into the next fold's test window.
    - Falls back to synthetic bars if the honba-data lakehouse is unavailable.
"""

from __future__ import annotations

import datetime as dt
import math
import sys
from copy import deepcopy
from typing import Any

from honba.algo_analytics.folds import Window, plan_folds
from honba.algo_analytics.walk_forward import (
    Fold,
    FoldStats,
    WalkForwardGates,
    WalkForwardResult,
    walk_forward,
)
from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma
from honba.strategies.testing import replay

from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.metrics import curve_metrics
from honba_examples.synthetic import oscillating_bars

__all__ = ["main", "run"]


class SmaWalk(Strategy):
    """SMA crossover with configurable periods."""

    name = "sma_walk_forward"

    def __init__(self, fast: int = 10, slow: int = 30, capital: float = 100_000.0) -> None:
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


def _evaluate_replay(bars_list: list, strategy: Strategy) -> FoldStats:
    """Run replay backtest and return FoldStats."""
    result = replay(strategy, bars_list)

    # Build equity curve from replay result
    cash = 100_000.0
    position = 0.0
    curve = [{"value": cash, "cash": cash, "date": str(bars_list[0].ts)}]
    for fill in result.fills:
        sign = 1.0 if fill.side.value == "buy" else -1.0
        notional = sign * fill.quantity * fill.price
        cash -= notional
        position += sign * fill.quantity
        curve.append({"value": cash + position * fill.price, "cash": cash, "date": str(fill.ts)})

    # Close open position
    if position > 0 and result.fills:
        last_price = result.fills[-1].price
        cash += position * last_price
        curve.append({"value": cash, "cash": cash, "date": str(result.fills[-1].ts + 1)})

    from datetime import datetime

    metric_curve = [
        {
            "date": datetime.fromtimestamp(int(c["date"]) / 1e9, tz=dt.UTC).date().isoformat(),
            "value": c["value"],
            "cash": c["cash"],
        }
        for c in curve
    ]

    metrics = curve_metrics(metric_curve, 100_000.0)

    n_fills = len(result.fills)
    n_trades = sum(1 for f in result.fills if f.side.value == "sell")

    return FoldStats(
        sharpe=metrics["sharpe"],
        total_return_pct=metrics["total_return_pct"],
        max_drawdown_pct=metrics["max_drawdown_pct"],
        final_equity=metrics["final_value"],
        n_fills=n_fills,
        n_trades=n_trades,
    )


def _run_synthetic_walk_forward(
    strategy: Strategy,
    symbol: str,
    start: str,
    end: str,
    train_months: int,
    test_months: int,
    n_folds: int,
    window: Window,
) -> WalkForwardResult:
    """Run walk-forward on synthetic data using replay."""
    start_d = dt.date.fromisoformat(start)
    end_d = dt.date.fromisoformat(end)
    days = (end_d - start_d).days
    n_bars = max(days * 5 // 7, 1000)

    # Generate bars for the full range
    bars_list = oscillating_bars(
        symbol, n_bars, start=start_d, amplitude=100.0, period=60, drift=2.0
    )

    # Plan folds
    folds_plan = plan_folds(
        start_d,
        end_d,
        train_months=train_months,
        test_months=test_months,
        n_folds=n_folds,
        window=window,
    )

    # Run each fold
    folds = []
    for i, w in enumerate(folds_plan):
        # Convert fold dates to bar indices
        train_start_idx = 0
        train_end_idx = int(len(bars_list) * (w.train_end - start_d).days / (end_d - start_d).days)
        test_start_idx = train_end_idx
        test_end_idx = int(len(bars_list) * (w.test_end - start_d).days / (end_d - start_d).days)

        train_bars = bars_list[train_start_idx:train_end_idx]
        test_bars = bars_list[test_start_idx:test_end_idx]

        if len(train_bars) < 30 or len(test_bars) < 30:
            # Not enough bars, skip
            continue

        # Fresh strategy instance per fold
        train_strategy = deepcopy(strategy)
        test_strategy = deepcopy(strategy)

        in_sample = _evaluate_replay(train_bars, train_strategy)
        out_of_sample = _evaluate_replay(test_bars, test_strategy)

        folds.append(
            Fold(
                index=i,
                train_start=w.train_start,
                train_end=w.train_end,
                test_start=w.test_start,
                test_end=w.test_end,
                in_sample=in_sample,
                out_of_sample=out_of_sample,
            )
        )

    return WalkForwardResult(
        folds=tuple(folds),
        train_months=train_months,
        test_months=test_months,
        window=window,
        gates=WalkForwardGates(),
    )


def run(
    symbol: str = "RELIANCE",
    exchange: str = "NSE",
    start: str | None = None,
    end: str | None = None,
    train_months: int = 24,
    test_months: int = 6,
    n_folds: int = 8,
    window: str = "expanding",
    fast: int = 10,
    slow: int = 30,
    cash: float = 1_000_000.0,
    use_synthetic: bool = False,
) -> dict[str, Any]:
    # Default dates
    if start is None:
        start = "2020-01-01"
    if end is None:
        end = "2024-12-31"

    # Create strategy instance
    strategy = SmaWalk(fast=fast, slow=slow)

    if use_synthetic:
        result = _run_synthetic_walk_forward(
            strategy,
            symbol,
            start,
            end,
            train_months,
            test_months,
            n_folds,
            window,  # type: ignore
        )
    else:
        # Use real data via honba.algo_analytics.walk_forward
        result = walk_forward(
            strategy,
            symbol=symbol,
            exchange=exchange,
            start=start,
            end=end,
            train_months=train_months,
            test_months=test_months,
            n_folds=n_folds,
            window=window,  # type: ignore
            cash=cash,
            data=None,  # Use lakehouse
        )

    # Build output
    folds_data = []
    for fold in result.folds:
        folds_data.append(
            {
                "fold": fold.index,
                "train_start": fold.train_start.isoformat(),
                "train_end": fold.train_end.isoformat(),
                "test_start": fold.test_start.isoformat(),
                "test_end": fold.test_end.isoformat(),
                "in_sample": {
                    "sharpe": fold.in_sample.sharpe,
                    "total_return_pct": fold.in_sample.total_return_pct,
                    "max_drawdown_pct": fold.in_sample.max_drawdown_pct,
                    "final_equity": fold.in_sample.final_equity,
                    "n_fills": fold.in_sample.n_fills,
                    "n_trades": fold.in_sample.n_trades,
                },
                "out_of_sample": {
                    "sharpe": fold.out_of_sample.sharpe,
                    "total_return_pct": fold.out_of_sample.total_return_pct,
                    "max_drawdown_pct": fold.out_of_sample.max_drawdown_pct,
                    "final_equity": fold.out_of_sample.final_equity,
                    "n_fills": fold.out_of_sample.n_fills,
                    "n_trades": fold.out_of_sample.n_trades,
                },
                "oos_is_ratio": fold.oos_is_ratio,
            }
        )

    # Walk-Forward Efficiency (mean OOS / mean IS Sharpe)
    is_sharpes = [f.in_sample.sharpe for f in result.folds]
    oos_sharpes = [f.out_of_sample.sharpe for f in result.folds]
    mean_is = sum(is_sharpes) / len(is_sharpes) if is_sharpes else 0.0
    mean_oos = sum(oos_sharpes) / len(oos_sharpes) if oos_sharpes else 0.0
    wfe = mean_oos / mean_is if mean_is > 0 else float("nan")

    return {
        "config": {
            "symbol": symbol,
            "exchange": exchange,
            "start": start,
            "end": end,
            "train_months": train_months,
            "test_months": test_months,
            "n_folds": n_folds,
            "window": window,
            "fast": fast,
            "slow": slow,
            "cash": cash,
            "use_synthetic": use_synthetic,
        },
        "gates": {
            "min_mean_oos_sharpe": result.gates.min_mean_oos_sharpe,
            "max_oos_sharpe_std": result.gates.max_oos_sharpe_std,
            "min_profitable_fraction": result.gates.min_profitable_fraction,
            "checks": result.checks,
            "passed": result.passed,
        },
        "wfe": {
            "mean_in_sample_sharpe": mean_is,
            "mean_out_of_sample_sharpe": mean_oos,
            "walk_forward_efficiency": wfe,
            "wfe_passed": wfe >= 0.60 if not math.isnan(wfe) else False,
        },
        "folds": folds_data,
        "summary": result.summary(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--symbol", type=str, default="RELIANCE")
    parser.add_argument("--exchange", type=str, default="NSE")
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--n-folds", type=int, default=8)
    parser.add_argument("--window", type=str, choices=["expanding", "rolling"], default="expanding")
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--cash", type=float, default=1_000_000.0)
    parser.add_argument(
        "--synthetic", action="store_true", help="Use synthetic bars instead of lakehouse"
    )
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(
        symbol=args.symbol,
        exchange=args.exchange,
        start=args.start,
        end=args.end,
        train_months=args.train_months,
        test_months=args.test_months,
        n_folds=args.n_folds,
        window=args.window,
        fast=args.fast,
        slow=args.slow,
        cash=args.cash,
        use_synthetic=args.synthetic,
    )

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
