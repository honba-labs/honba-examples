"""backtesting/06_concurrent_backtest: parameter sweep with concurrent execution.

Uses ``honba.strategies.testing.replay`` (bar-close fills, no settlement or costs)
in a thread pool to evaluate a grid of (fast, slow)
periods on deterministic noisy sine-wave bars. Reports the Pareto frontier (return vs drawdown)
and the best by Sharpe among pairs with enough closed trades; pairs with too few are listed as
``insufficient_trades`` and never ranked. The output carries a ``multiple_testing`` block with the
number of combinations tried.

Run::

    python backtesting/06_concurrent_backtest.py --workers 4

Pitfalls::

    - Multiple testing: the default grid is 56 (fast, slow) pairs scored on one history, and
      best_by_sharpe is the maximum of up to 56 noisy numbers, which is biased upward. The output
      prints the trial count; also deflate the Sharpe and look for a plateau of good neighbours
      instead of a lone peak.
    - No out-of-sample: the same 300 bars select the winner and report it. 04_walk_forward is the
      missing step; a sweep result is a hypothesis, not a finding.
    - Not enough trades is not zero risk: a pair with fewer than --min-trades closed trades has no
      meaningful Sharpe or drawdown, so it is reported as insufficient_trades rather than ranked.
      Even ranked pairs rest on a handful of trades; check the fills column before trusting a rank.
    - Defence shown: results are collected in grid order, not completion order, so thread scheduling
      cannot change which of several equal scores wins.
    - The per-pair curve is rebuilt from fills alone (no mark-to-market between trades, no costs,
      bar-close fills), so Sharpe and drawdown rest on a handful of points; short periods trade more
      and would look better here than they would after costs.
"""

from __future__ import annotations

import concurrent.futures
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma
from honba.strategies.testing import replay

from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.metrics import curve_metrics
from honba_examples.synthetic import oscillating_bars

__all__ = ["main", "run"]


class SmaSweep(Strategy):
    """SMA crossover for parameter sweeps."""

    name = "sma_sweep"
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
        elif fast_val < slow_val and position > 0:
            self.sell(bar.instrument_id, position)


def _backtest(bars_list: list, fast: int, slow: int, min_trades: int = 3) -> dict[str, Any]:
    strat = SmaSweep(fast=fast, slow=slow)
    result = replay(strat, bars_list)

    round_trips = sum(1 for f in result.fills if f.side.value == "sell")
    if round_trips < min_trades:
        # Too few trades for a Sharpe or drawdown to mean anything: report, do not rank
        return {
            "fast": fast,
            "slow": slow,
            "fills": len(result.fills),
            "status": "insufficient_trades",
            "final_equity": None,
            "return_pct": None,
            "max_dd_pct": None,
            "sharpe": None,
        }

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
            "date": datetime.fromtimestamp(int(c["date"]) / 1e9, tz=timezone.utc)
            .date()
            .isoformat(),
            "value": c["value"],
            "cash": c["cash"],
        }
        for c in curve
    ]

    metrics = curve_metrics(metric_curve, 100_000.0)
    return {
        "fast": fast,
        "slow": slow,
        "fills": len(result.fills),
        "status": "ok",
        "final_equity": metrics["final_value"],
        "return_pct": metrics["total_return_pct"],
        "max_dd_pct": metrics["max_drawdown_pct"],
        "sharpe": metrics["sharpe"],
    }


def run(
    bars: int = 300,
    fast_range: tuple[int, int] = (5, 20),
    slow_range: tuple[int, int] = (20, 50),
    workers: int = 4,
    min_trades: int = 3,
) -> dict[str, Any]:
    bars_list = oscillating_bars(
        "RELIANCE", bars, amplitude=60.0, period=40, drift=3.0, noise=30.0, seed=7
    )

    # Generate parameter grid
    params = [
        (f, s)
        for f in range(fast_range[0], fast_range[1] + 1, 2)
        for s in range(slow_range[0], slow_range[1] + 1, 5)
        if f < s
    ]

    # Concurrent execution
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        # Collect in grid order, not completion order: thread scheduling must not
        # decide the order of equal-scoring results.
        futures = [executor.submit(_backtest, bars_list, f, s, min_trades) for f, s in params]
        results = [future.result() for future in futures]

    # Only pairs with enough closed trades are ranked; the rest are listed as insufficient
    ranked = [r for r in results if r["status"] == "ok"]
    insufficient = [r for r in results if r["status"] != "ok"]

    ranked.sort(key=lambda r: (-r["sharpe"], r["fast"], r["slow"]))
    best_sharpe = ranked[0] if ranked else None

    # Pareto: max return for each drawdown bucket
    ranked.sort(key=lambda r: (r["max_dd_pct"], r["fast"], r["slow"]))
    pareto = []
    best_ret = -float("inf")
    for r in ranked:
        if r["return_pct"] > best_ret:
            pareto.append(r)
            best_ret = r["return_pct"]

    note = (
        f"{len(params)} (fast, slow) combinations were tried on one history; the best Sharpe is "
        "the maximum of that many noisy numbers and is biased upward."
    )
    if insufficient:
        note += (
            f" {len(insufficient)} had fewer than {min_trades} closed trades (insufficient_trades)"
            " and were not ranked."
        )

    return {
        "config": {
            "bars": bars,
            "fast_range": fast_range,
            "slow_range": slow_range,
            "workers": workers,
            "min_trades": min_trades,
        },
        "total_combinations": len(params),
        "multiple_testing": {
            "combinations_tried": len(params),
            "ranked": len(ranked),
            "insufficient_trades": len(insufficient),
            "note": note,
        },
        "best_by_sharpe": best_sharpe,
        "pareto_frontier": pareto,
        "all": ranked + insufficient,
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--bars", type=int, default=300)
    parser.add_argument("--fast-min", type=int, default=5)
    parser.add_argument("--fast-max", type=int, default=20)
    parser.add_argument("--slow-min", type=int, default=20)
    parser.add_argument("--slow-max", type=int, default=50)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--min-trades", type=int, default=3, help="closed trades needed for a pair to be ranked"
    )
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(
        bars=args.bars,
        fast_range=(args.fast_min, args.fast_max),
        slow_range=(args.slow_min, args.slow_max),
        workers=args.workers,
        min_trades=args.min_trades,
    )

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
