"""optimizer/02_bayesian_mean_reversion: Bayesian hyperparameter optimization (TPE) of a mean-reversion strategy.

Optimizes the parameters of an RSI-based mean-reversion strategy using
Tree-structured Parzen Estimators (TPE) via ``honba_ya_enhancer.BayesianOptimizer``.
TPE models the posterior distribution of good vs bad parameters and samples
promising regions, requiring far fewer evaluations than grid or random search.

Run::

    python examples/optimizer/02_bayesian_mean_reversion.py --trials 100

Pitfalls::

    - TPE builds a density model from evaluations; it needs enough initial
      random points (default 20) before the model is informative. With too few
      trials, it degenerates to random search.
    - The acquisition function (Expected Improvement by default) balances
      exploration vs exploitation. ``kappa`` controls UCB exploration if used.
    - Parameter bounds must be set before optimization. The search space here is:
      RSI period [7, 21], oversold [20, 40], overbought [60, 80], lookback [5, 30].
    - Like GA, Bayesian optimization evaluates on fixed in-sample data. The best
      parameters *must* be validated out-of-sample (WFO) before any deployment.
    - TPE assumes parameters are continuous. Integer parameters (RSI period)
      are rounded during evaluation. For purely categorical parameters,
      use a different optimizer.
    - The strategy uses synthetic bars by default. Real data requires the
      honba-data lakehouse.
"""

from __future__ import annotations

import random
import sys
from typing import Any

from honba.strategies.base import Strategy
from honba.strategies.indicators import Rsi

from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.synthetic import oscillating_bars

__all__ = ["main", "run"]

CAPITAL = 100_000.0


class RsiMeanReversion(Strategy):
    """RSI mean-reversion with configurable thresholds."""

    name = "rsi_mean_reversion"

    def __init__(
        self,
        rsi_period: int = 14,
        oversold: int = 30,
        overbought: int = 70,
        lookback: int = 10,
        capital: float = CAPITAL,
    ) -> None:
        super().__init__()
        self.capital = capital
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
        self.lookback = lookback

        self.rsi = Rsi(rsi_period)
        self.recent_closes: list[float] = []

    def on_bar(self, bar) -> None:
        rsi_val = self.rsi.update(bar.close)

        if rsi_val is None:
            return

        self.recent_closes.append(bar.close)
        if len(self.recent_closes) > self.lookback:
            self.recent_closes.pop(0)

        position = self.position(bar.instrument_id)

        # Entry: RSI oversold
        if position <= 0 and rsi_val < self.oversold:
            if len(self.recent_closes) >= self.lookback:
                qty = int(self.capital // bar.close)
                if qty > 0:
                    self.buy(bar.instrument_id, qty)

        # Exit: RSI overbought
        elif position > 0 and rsi_val > self.overbought:
            self.sell(bar.instrument_id, position)


def _evaluate(bars_list: list, params: dict[str, Any]) -> dict[str, Any]:
    """Run backtest and return metrics."""
    from honba.strategies.testing import replay

    from honba_examples.metrics import curve_metrics

    strat = RsiMeanReversion(**params)
    result = replay(strat, bars_list)

    cash, position = CAPITAL, 0.0
    curve = [{"date": str(bars_list[0].ts), "value": cash, "cash": cash}]
    for fill in result.fills:
        sign = 1.0 if fill.side.value == "buy" else -1.0
        notional = sign * fill.quantity * fill.price
        cash -= notional
        position += sign * fill.quantity
        curve.append({"date": str(fill.ts), "value": cash + position * fill.price, "cash": cash})

    if position > 0 and result.fills:
        last_price = result.fills[-1].price
        cash += position * last_price
        curve.append({"date": str(result.fills[-1].ts + 1), "value": cash, "cash": cash})

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

    n_trades = sum(1 for f in result.fills if f.side.value == "sell")

    return {
        **params,
        "return_pct": metrics["total_return_pct"],
        "max_dd_pct": metrics["max_drawdown_pct"],
        "sharpe": metrics["sharpe"],
        "n_trades": n_trades,
        "final_equity": metrics["final_value"],
        "status": "ok" if n_trades >= 2 else "insufficient_trades",
    }


class SimpleTPEOptimizer:
    """Simplified TPE optimizer for demonstration.

    This is a pure-Python implementation showing the TPE structure.
    Production code should use the Rust ``honba_ya_enhancer.BayesianOptimizer``.
    """

    def __init__(
        self,
        param_bounds: dict[str, tuple],
        initial_points: int = 20,
        total_points: int = 100,
        seed: int = 42,
    ):
        self.param_bounds = param_bounds
        self.param_names = list(param_bounds.keys())
        self.initial_points = initial_points
        self.total_points = total_points
        self.rng = random.Random(seed)

        self.evaluated: list[tuple[dict[str, Any], float]] = []
        self.best_params: dict[str, Any] | None = None
        self.best_fitness: float = float("-inf")

    def _random_params(self) -> dict[str, Any]:
        params = {}
        for name, (min_v, max_v) in self.param_bounds.items():
            if isinstance(min_v, int):
                params[name] = self.rng.randint(min_v, max_v)
            else:
                params[name] = round(self.rng.uniform(min_v, max_v), 2)
        return params

    def _fitness(self, eval_result: dict[str, Any]) -> float:
        if eval_result["status"] != "ok":
            return -100.0
        # Composite fitness: Sharpe * 0.5 + Return/DD * 0.3 + trades * 0.2
        return (
            eval_result["sharpe"] * 0.5
            + (eval_result["return_pct"] / max(0.1, abs(eval_result["max_dd_pct"]))) * 0.3
            + min(eval_result["n_trades"], 20) * 0.01
        )

    def optimize(self, bars_list: list) -> dict[str, Any]:
        # Initial random evaluations
        for _ in range(self.initial_points):
            params = self._random_params()
            result = _evaluate(bars_list, params)
            fitness = self._fitness(result)
            self.evaluated.append((params, fitness))

            if fitness > self.best_fitness:
                self.best_fitness = fitness
                self.best_params = params

        # TPE loop (simplified: random search with bias toward good regions)
        for iteration in range(self.total_points - self.initial_points):
            # In real TPE, we'd build KDE models for good/bad params and sample from ratio
            # Here we do a simple biased sampling: perturb best params with probability
            if self.best_params and self.rng.random() < 0.7:
                # Exploit: perturb best
                params = self.best_params.copy()
                for name, (min_v, max_v) in self.param_bounds.items():
                    if self.rng.random() < 0.3:
                        if isinstance(min_v, int):
                            delta = self.rng.randint(-2, 2)
                            params[name] = max(min_v, min(max_v, params[name] + delta))
                        else:
                            delta = self.rng.gauss(0, (max_v - min_v) * 0.1)
                            params[name] = round(max(min_v, min(max_v, params[name] + delta)), 2)
            else:
                # Explore: random
                params = self._random_params()

            result = _evaluate(bars_list, params)
            fitness = self._fitness(result)
            self.evaluated.append((params, fitness))

            if fitness > self.best_fitness:
                self.best_fitness = fitness
                self.best_params = params

            if iteration % 20 == 0:
                print(
                    f"TPE iteration {iteration}: best_fitness={self.best_fitness:.4f}",
                    file=sys.stderr,
                )

        return {
            "best_params": self.best_params,
            "best_fitness": self.best_fitness,
            "evaluations": len(self.evaluated),
            "all_results": [{"params": p, "fitness": f} for p, f in self.evaluated],
        }


def run(
    bars: int = 500,
    trials: int = 100,
    initial_points: int = 20,
    seed: int = 42,
) -> dict[str, Any]:
    bars_list = oscillating_bars(
        "RELIANCE", bars, amplitude=60.0, period=50, drift=1.5, noise=20.0, seed=seed
    )

    param_bounds = {
        "rsi_period": (7, 21),
        "oversold": (20, 40),
        "overbought": (60, 80),
        "lookback": (5, 30),
    }

    optimizer = SimpleTPEOptimizer(
        param_bounds=param_bounds,
        initial_points=initial_points,
        total_points=trials,
        seed=seed,
    )

    result = optimizer.optimize(bars_list)

    # Extract Pareto-like frontier from evaluations
    all_results = []
    for params, fitness in optimizer.evaluated:
        eval_result = _evaluate(bars_list, params)
        all_results.append(eval_result)

    from honba_examples.optimizer import ParetoPoint, extract_pareto_frontier, plot_pareto

    pareto = extract_pareto_frontier(all_results)

    return {
        "config": {
            "bars": bars,
            "trials": trials,
            "initial_points": initial_points,
            "seed": seed,
            "param_bounds": param_bounds,
        },
        "best_params": result["best_params"],
        "best_fitness": result["best_fitness"],
        "total_evaluations": result["evaluations"],
        "pareto_frontier": [
            {
                "fast": p.fast,
                "slow": p.slow,
                "return_pct": p.return_pct,
                "max_dd_pct": p.max_dd_pct,
                "sharpe": p.sharpe,
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
                for e in all_results
                if e.get("status") == "ok"
            ],
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--bars", type=int, default=500)
    parser.add_argument("--trials", type=int, default=100)
    parser.add_argument("--initial-points", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(
        bars=args.bars,
        trials=args.trials,
        initial_points=args.initial_points,
        seed=args.seed,
    )

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
