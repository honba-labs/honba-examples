"""optimizer/01_ga_trend_breakout: Genetic Algorithm optimization of a trend-following strategy.

Optimizes the parameters of an SMA crossover strategy (fast/slow periods, RSI filter,
ATR trailing multiplier) using a multi-objective Genetic Algorithm. Reports the Pareto
frontier (Sharpe vs Max Drawdown) and the best parameters by a composite fitness score.

Run::

    python examples/optimizer/01_ga_trend_breakout.py --generations 50 --population 50

Pitfalls::

    - The GA evaluates many parameter combinations on the same in-sample data.
      The best in-sample fitness is biased upward (multiple testing). Always
      validate the chosen parameters out-of-sample (see Phase 2.3 WFO).
    - Population size and generations trade off search breadth vs runtime.
      Defaults (pop=50, gen=50) yield 2,500 evaluations; each is a full backtest.
    - Fitness is a weighted combination of Sharpe, Sortino, Profit Factor, and
      inverse Drawdown. A single peak may be noise; prefer a plateau of good
      neighbours. The Pareto frontier visualises this.
    - The strategy uses synthetic deterministic bars by default. For real data,
      pass ``--symbol RELIANCE --start 2020-01-01 --end 2024-12-31`` (requires
      honba-data lakehouse).
    - The GA operates on strategy *instances*. Each evaluation gets a fresh copy
      so state cannot leak between individuals, but the same bars are reused.
"""

from __future__ import annotations

import random
import sys
from typing import Any

from honba.strategies.base import Strategy
from honba.strategies.indicators import Atr, Rsi, Sma

from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.optimizer import (
    ParetoPoint,
    extract_pareto_frontier,
    plot_pareto,
    sharpe_sortino_dd,
)
from honba_examples.synthetic import oscillating_bars

__all__ = ["main", "run"]

CAPITAL = 100_000.0


class SmaTrendFollowing(Strategy):
    """SMA crossover trend-following with RSI filter and ATR trailing stop."""

    name = "sma_trend_following"

    def __init__(
        self,
        fast: int = 10,
        slow: int = 30,
        rsi_period: int = 14,
        rsi_threshold: int = 50,
        atr_period: int = 14,
        atr_mult: float = 2.0,
        capital: float = CAPITAL,
    ) -> None:
        super().__init__()
        self.capital = capital
        self.fast = Sma(fast)
        self.slow = Sma(slow)
        self.rsi = Rsi(rsi_period)
        self.rsi_threshold = rsi_threshold
        self.atr = Atr(atr_period)
        self.atr_mult = atr_mult

        self.entry_price: float | None = None
        self.trail_price: float | None = None

    def on_bar(self, bar) -> None:
        fast_val = self.fast.update(bar.close)
        slow_val = self.slow.update(bar.close)
        rsi_val = self.rsi.update(bar.close)
        atr_val = self.atr.update(bar.high, bar.low, bar.close)

        if fast_val is None or slow_val is None or rsi_val is None or atr_val is None:
            return

        position = self.position(bar.instrument_id)

        # Entry: fast SMA crosses above slow SMA AND RSI > threshold (trend confirmation)
        if position <= 0 and fast_val > slow_val and rsi_val > self.rsi_threshold:
            qty = int(self.capital // bar.close)
            if qty > 0:
                self.buy(bar.instrument_id, qty)
                self.entry_price = bar.close
                self.trail_price = bar.close - atr_val * self.atr_mult

        # Exit: fast SMA crosses below slow SMA OR ATR trailing stop hit
        elif position > 0:
            # Update trailing stop
            new_trail = bar.close - atr_val * self.atr_mult
            if new_trail > (self.trail_price or 0):
                self.trail_price = new_trail

            # Check exit conditions
            if fast_val < slow_val or (self.trail_price and bar.low <= self.trail_price):
                self.sell(bar.instrument_id, position)
                self.entry_price = None
                self.trail_price = None


def _evaluate(bars_list: list, params: dict[str, Any]) -> dict[str, Any]:
    """Run backtest and return metrics."""
    from honba.strategies.testing import replay

    from honba_examples.metrics import curve_metrics

    strat = SmaTrendFollowing(**params)
    result = replay(strat, bars_list)

    # Build equity curve
    cash, position = CAPITAL, 0.0
    curve = [{"date": str(bars_list[0].ts), "value": cash, "cash": cash}]
    for fill in result.fills:
        sign = 1.0 if fill.side.value == "buy" else -1.0
        notional = sign * fill.quantity * fill.price
        cash -= notional
        position += sign * fill.quantity
        curve.append({"date": str(fill.ts), "value": cash + position * fill.price, "cash": cash})

    # Close open position
    if position > 0 and result.fills:
        last_price = result.fills[-1].price
        cash += position * last_price
        curve.append({"date": str(result.fills[-1].ts + 1), "value": cash, "cash": cash})

    # Compute metrics
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

    metrics = curve_metrics(metric_curve, CAPITAL)

    # Count trades
    n_trades = sum(1 for f in result.fills if f.side.value == "sell")

    # Profit factor
    gross_profit = 0.0
    gross_loss = 0.0
    entry_price = None
    for f in result.fills:
        if f.side.value == "buy":
            entry_price = f.price
        elif f.side.value == "sell" and entry_price is not None:
            pnl = (f.price - entry_price) * f.quantity
            if pnl > 0:
                gross_profit += pnl
            else:
                gross_loss += abs(pnl)
            entry_price = None

    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    return {
        **params,
        "return_pct": metrics["total_return_pct"],
        "max_dd_pct": metrics["max_drawdown_pct"],
        "sharpe": metrics["sharpe"],
        "sortino": metrics.get("sortino", 0.0),
        "profit_factor": profit_factor,
        "n_trades": n_trades,
        "final_equity": metrics["final_value"],
        "status": "ok" if n_trades >= 2 else "insufficient_trades",
    }


def run(
    bars: int = 500,
    generations: int = 50,
    population: int = 50,
    seed: int = 42,
) -> dict[str, Any]:
    rng = random.Random(seed)

    # Generate synthetic bars with trend + noise
    bars_list = oscillating_bars(
        "RELIANCE", bars, amplitude=80.0, period=60, drift=2.0, noise=25.0, seed=seed
    )

    # Parameter bounds
    param_bounds = {
        "fast": (5, 20),
        "slow": (20, 60),
        "rsi_period": (7, 21),
        "rsi_threshold": (40, 70),
        "atr_period": (10, 30),
        "atr_mult": (1.0, 4.0),
    }

    # Initialize population
    pop = []
    for _ in range(population):
        individual = {
            "fast": rng.randint(*param_bounds["fast"]),
            "slow": rng.randint(*param_bounds["slow"]),
            "rsi_period": rng.randint(*param_bounds["rsi_period"]),
            "rsi_threshold": rng.randint(*param_bounds["rsi_threshold"]),
            "atr_period": rng.randint(*param_bounds["atr_period"]),
            "atr_mult": round(rng.uniform(*param_bounds["atr_mult"]), 2),
        }
        # Ensure fast < slow
        if individual["fast"] >= individual["slow"]:
            individual["slow"] = individual["fast"] + 5
        pop.append(individual)

    # Track all evaluations for Pareto
    all_evaluations: list[dict[str, Any]] = []
    best_individual = None
    best_fitness = float("-inf")

    for gen in range(generations):
        # Evaluate population
        fitnesses = []
        for ind in pop:
            eval_result = _evaluate(bars_list, ind)
            all_evaluations.append(eval_result)

            if eval_result["status"] == "ok":
                fitness = sharpe_sortino_dd(
                    eval_result["sharpe"],
                    eval_result.get("sortino", 0.0),
                    eval_result.get("profit_factor", 1.0),
                    eval_result["max_dd_pct"],
                )
            else:
                fitness = -100.0  # Penalize insufficient trades

            fitnesses.append(fitness)

            if fitness > best_fitness:
                best_fitness = fitness
                best_individual = ind.copy()

        # Tournament selection
        new_pop = []
        tournament_size = 3

        for _ in range(population):
            candidates = rng.sample(range(population), min(tournament_size, population))
            winner = max(candidates, key=lambda i: fitnesses[i])
            new_pop.append(pop[winner].copy())

        # Crossover
        for i in range(0, population - 1, 2):
            if rng.random() < 0.8:  # crossover_rate
                p1, p2 = new_pop[i], new_pop[i + 1]
                child1, child2 = {}, {}
                for key in param_bounds:
                    if rng.random() < 0.5:
                        child1[key], child2[key] = p1[key], p2[key]
                    else:
                        child1[key], child2[key] = p2[key], p1[key]
                # Ensure fast < slow for children
                for child in (child1, child2):
                    if child["fast"] >= child["slow"]:
                        child["slow"] = child["fast"] + 5
                new_pop[i], new_pop[i + 1] = child1, child2

        # Mutation
        for ind in new_pop:
            for key, (min_v, max_v) in param_bounds.items():
                if rng.random() < 0.2:  # mutation_rate
                    if isinstance(min_v, int):
                        delta = rng.randint(-3, 3)
                        ind[key] = max(min_v, min(max_v, ind[key] + delta))
                    else:
                        delta = rng.gauss(0, (max_v - min_v) * 0.1)
                        ind[key] = round(max(min_v, min(max_v, ind[key] + delta)), 2)
            # Ensure fast < slow after mutation
            if ind["fast"] >= ind["slow"]:
                ind["slow"] = ind["fast"] + 5

        # Elitism: keep best
        if best_individual:
            new_pop[0] = best_individual

        pop = new_pop

        if gen % 10 == 0:
            print(f"Generation {gen}: best_fitness={best_fitness:.4f}", file=sys.stderr)

    # Extract Pareto frontier
    pareto = extract_pareto_frontier(all_evaluations)

    return {
        "config": {
            "bars": bars,
            "generations": generations,
            "population": population,
            "seed": seed,
            "param_bounds": param_bounds,
        },
        "best_params": best_individual,
        "best_fitness": best_fitness,
        "total_evaluations": len(all_evaluations),
        "pareto_frontier": [
            {
                "fast": p.fast,
                "slow": p.slow,
                "return_pct": p.return_pct,
                "max_dd_pct": p.max_dd_pct,
                "sharpe": p.sharpe,
                "sortino": p.sortino,
                "profit_factor": p.profit_factor,
                "n_trades": p.n_trades,
            }
            for p in pareto
        ],
        "pareto_plot": plot_pareto(
            pareto,
            [
                ParetoPoint(
                    fast=e.get("fast", 0),
                    slow=e.get("slow", 0),
                    return_pct=e.get("return_pct", 0.0),
                    max_dd_pct=e.get("max_dd_pct", 0.0),
                    sharpe=e.get("sharpe", 0.0),
                    sortino=e.get("sortino"),
                    profit_factor=e.get("profit_factor"),
                    n_trades=e.get("n_trades", 0),
                )
                for e in all_evaluations
                if e.get("status") == "ok"
            ],
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--bars", type=int, default=500)
    parser.add_argument("--generations", type=int, default=50)
    parser.add_argument("--population", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(
        bars=args.bars,
        generations=args.generations,
        population=args.population,
        seed=args.seed,
    )

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
