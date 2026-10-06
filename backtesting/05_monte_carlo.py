"""backtesting/05_monte_carlo: bootstrap equity curves to estimate risk.

Resamples the fills from a base backtest with replacement, rebuilding the equity
curve many times to build a distribution of final equity / max drawdown / Sharpe.
This is the simplest Monte Carlo (i.i.d. resampling); production uses block
bootstrap and parameter sweeps.

Run::

    python backtesting/05_monte_carlo.py --paths 1000
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
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


class SmaMonteCarlo(Strategy):
    """SMA crossover for Monte Carlo bootstrap."""

    name: str = "sma_monte_carlo"
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


def _equity_curve_from_fills(fills: list, capital: float) -> list[dict[str, Any]]:
    """Reconstruct equity curve from a list of fills (simplified: no costs).

    If there's only one type of fill (e.g. all buys), add a closing fill at the
    end so the equity curve has a meaningful path.
    """
    if not fills:
        return []

    # Ensure we have both buy and sell for a round trip
    has_buy = any(f.side.value == "buy" for f in fills)
    has_sell = any(f.side.value == "sell" for f in fills)

    cash = capital
    position = 0.0
    curve = []

    for fill in fills:
        notional = fill.quantity * fill.price
        if fill.side.value == "buy":
            cash -= notional
            position += fill.quantity
        else:
            cash += notional
            position -= fill.quantity
        equity = cash + position * fill.price
        curve.append({"date": fill.ts, "value": equity, "cash": cash})

    # If never sold, close at last fill's price
    if has_buy and not has_sell and position > 0:
        last = fills[-1]
        notional = position * last.price
        cash += notional
        equity = cash
        curve.append({"date": last.ts + 1, "value": equity, "cash": cash})

    return curve


def run(
    bars: int = 100,
    fast: int = 10,
    slow: int = 30,
    paths: int = 200,
    seed: int = 42,
) -> dict[str, Any]:
    random.seed(seed)

    start = dt.date(2026, 1, 1)
    bars_list = [synth_bar("RELIANCE", d, 2500.0 + i * 0.5) for i, d in enumerate(weekdays_func(start, bars))]

    # Base backtest
    strat = SmaMonteCarlo(fast=fast, slow=slow)
    base = replay(strat, bars_list)

    # Extract fill returns (simplified: each fill's PnL contribution)
    base_fills = base.fills

    # Monte Carlo: resample fills with replacement
    final_equities = []
    max_drawdowns = []
    sharpes = []

    for _ in range(paths):
        resampled = random.choices(base_fills, k=len(base_fills))
        curve = _equity_curve_from_fills(resampled, 100_000.0)
        if len(curve) < 2:
            continue
        # Format for curve_metrics: dates must be ISO YYYY-MM-DD strings
        from datetime import datetime, timezone
        metric_curve = [
            {
                "date": datetime.fromtimestamp(c["date"] / 1e9, tz=timezone.utc).date().isoformat(),
                "value": c["value"],
                "cash": c["cash"]
            }
            for c in curve
        ]
        metrics = curve_metrics(metric_curve, 100_000.0)
        final_equities.append(metrics["final_value"])
        max_drawdowns.append(metrics["max_drawdown_pct"])
        sharpes.append(metrics["sharpe"])

    return {
        "config": {"bars": bars, "fast": fast, "slow": slow, "paths": paths, "seed": seed},
        "base": {"fills": len(base_fills)},
        "mc": {
            "final_equity": {"mean": sum(final_equities) / len(final_equities), "p5": sorted(final_equities)[int(0.05 * len(final_equities))], "p95": sorted(final_equities)[int(0.95 * len(final_equities))]},
            "max_drawdown_pct": {"mean": sum(max_drawdowns) / len(max_drawdowns), "p5": sorted(max_drawdowns)[int(0.05 * len(max_drawdowns))], "p95": sorted(max_drawdowns)[int(0.95 * len(max_drawdowns))]},
            "sharpe": {"mean": sum(sharpes) / len(sharpes), "p5": sorted(sharpes)[int(0.05 * len(sharpes))], "p95": sorted(sharpes)[int(0.95 * len(sharpes))]},
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bars", type=int, default=100)
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--paths", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(bars=args.bars, fast=args.fast, slow=args.slow, paths=args.paths, seed=args.seed)

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())