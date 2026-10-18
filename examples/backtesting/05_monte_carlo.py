"""backtesting/05_monte_carlo: bootstrap closed trades to estimate risk.

Pairs the fills of a base backtest into closed round-trip trades, resamples whole trades (their
returns) with replacement and compounds each draw into an equity path, many times, to build a
distribution of final equity / max drawdown / Sharpe. This is the simplest trade-level Monte Carlo
(i.i.d. over trades); production uses block bootstrap and parameter sweeps. It refuses to run on
fewer than ``--min-trades`` closed trades.

Run::

    python backtesting/05_monte_carlo.py --paths 1000

Pitfalls::

    - Trades are treated as i.i.d.: resampling whole round trips never makes an impossible
      buy/sell sequence, but it still destroys serial dependence (loss clusters, regime changes), so
      the drawdown tails are optimistic. Block bootstrap on trades or returns respects it.
    - Few trades, wide uncertainty: the default run has 9 closed trades, just over the minimum of 8.
      Monte Carlo cannot add information the sample lacks; with so few trades the bands describe the
      sample, not the strategy. An open final position is dropped, not marked.
    - Each path compounds fully invested, gross of costs, with one point per trade (on the base
      trade dates), so drawdown is measured between trade exits, not mark-to-market daily, and the
      Sharpe is annualised from those sparse points and is not comparable to a daily Sharpe.
    - Percentile indexing is int(p * n) on the sorted list, so with fewer than 20 paths the 'p5' is
      the minimum. Use 1000 or more paths for tails, and report spread across several --seed values.
    - The seed makes the draw reproducible (a private random.Random, the global generator is left
      alone), but one seed is one draw.
"""

from __future__ import annotations

import random
import sys
from datetime import datetime, timezone
from typing import Any, NamedTuple

from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma
from honba.strategies.testing import replay

from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.metrics import curve_metrics
from honba_examples.synthetic import oscillating_bars

__all__ = ["main", "run"]

CAPITAL = 100_000.0


class SmaMonteCarlo(Strategy):
    """SMA crossover for Monte Carlo bootstrap."""

    name = "sma_monte_carlo"
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


class RoundTrip(NamedTuple):
    """One closed long trade: entry, exit and the return on the entry price."""

    entry_price: float
    exit_price: float
    exit_ts: int

    @property
    def ret(self) -> float:
        return self.exit_price / self.entry_price - 1.0


def _round_trips(fills: list) -> list[RoundTrip]:
    """Pair each buy with the next sell; a final unmatched buy (open position) is dropped."""
    trips: list[RoundTrip] = []
    entry: float | None = None
    for f in fills:
        if f.side.value == "buy":
            entry = f.price
        elif entry is not None:
            trips.append(RoundTrip(entry, f.price, f.ts))
            entry = None
    return trips


def _iso(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).date().isoformat()


def _bootstrap_curve(returns: list[float], capital: float, dates: list[str]) -> list[dict]:
    """Equity after each trade when ``capital`` is fully invested and compounds ``returns``.

    ``dates`` has one more entry than ``returns``: the start and the exit date of each slot.
    """
    value = capital
    curve = [{"date": dates[0], "value": value, "cash": value}]
    for r, day in zip(returns, dates[1:], strict=True):
        value *= 1.0 + r
        curve.append({"date": day, "value": value, "cash": value})
    return curve


def _summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    n = len(ordered)
    return {
        "mean": sum(ordered) / n,
        "p5": ordered[int(0.05 * n)],
        "p95": ordered[min(n - 1, int(0.95 * n))],
    }


def run(
    bars: int = 400,
    fast: int = 10,
    slow: int = 30,
    paths: int = 200,
    seed: int = 42,
    min_trades: int = 8,
) -> dict[str, Any]:
    rng = random.Random(seed)

    bars_list = oscillating_bars(
        "RELIANCE", bars, amplitude=60.0, period=40, drift=3.0, noise=30.0, seed=7
    )

    # Base backtest
    base = replay(SmaMonteCarlo(fast=fast, slow=slow), bars_list)
    trips = _round_trips(base.fills)
    if len(trips) < min_trades:
        raise ValueError(
            f"only {len(trips)} closed round-trip trades; a trade bootstrap needs at least "
            f"{min_trades} (more bars, or a faster signal). Resampling fewer trades only "
            "reports the sample back as false precision."
        )

    # Monte Carlo: resample whole round-trip trades with replacement; each path compounds the
    # drawn trade returns in drawn order on the slots of the base trade dates
    returns = [t.ret for t in trips]
    first_entry = _iso(base.fills[0].ts)
    dates = [first_entry] + [_iso(t.exit_ts) for t in trips]
    final_equities, max_drawdowns, sharpes = [], [], []
    for _ in range(paths):
        drawn = rng.choices(returns, k=len(returns))
        metrics = curve_metrics(_bootstrap_curve(drawn, CAPITAL, dates), CAPITAL)
        final_equities.append(metrics["final_value"])
        max_drawdowns.append(metrics["max_drawdown_pct"])
        sharpes.append(metrics["sharpe"])

    return {
        "config": {
            "bars": bars,
            "fast": fast,
            "slow": slow,
            "paths": paths,
            "seed": seed,
            "min_trades": min_trades,
        },
        "base": {
            "fills": len(base.fills),
            "round_trips": len(trips),
            "mean_trade_return": sum(returns) / len(returns),
        },
        "mc": {
            "final_equity": _summary(final_equities),
            "max_drawdown_pct": _summary(max_drawdowns),
            "sharpe": _summary(sharpes),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--bars", type=int, default=400)
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--paths", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--min-trades", type=int, default=8, help="minimum closed round trips to bootstrap"
    )
    add_out_arg(parser)
    args = parser.parse_args(argv)

    try:
        result = run(
            bars=args.bars,
            fast=args.fast,
            slow=args.slow,
            paths=args.paths,
            seed=args.seed,
            min_trades=args.min_trades,
        )
    except ValueError as exc:
        parser.error(str(exc))

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
