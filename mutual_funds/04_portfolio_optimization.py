"""mutual_funds/04_portfolio_optimization: Mean-variance optimization on fund NAVs.

Loads NAV history for multiple schemes, computes expected returns and covariance,
runs mean-variance optimization with constraints (max weight, min allocation).

Run::

    python mutual_funds/04_portfolio_optimization.py --schemes 120503,120504,120505 --start 2022-01-01
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

import numpy as np
from scipy.optimize import minimize

from honba_examples.amfi import AmfiNavLoader

__all__ = ["main", "run"]


def _align_navs(nav_dict: dict[str, list], start: dt.date, end: dt.date) -> dict[str, list[float]]:
    """Align all NAV series to common dates, forward-fill missing."""
    all_dates: set[dt.date] = set()
    for points in nav_dict.values():
        all_dates.update(p.date for p in points)
    common = sorted(d for d in all_dates if start <= d <= end)

    aligned = {}
    for scheme, points in nav_dict.items():
        nav_by_date = {p.date: p.nav for p in points}
        series = []
        last_nav = None
        for d in common:
            if d in nav_by_date:
                last_nav = nav_by_date[d]
            if last_nav is not None:
                series.append(last_nav)
        aligned[scheme] = series
    return aligned


def _returns(series: list[float]) -> list[float]:
    return [(series[i] / series[i - 1]) - 1 for i in range(1, len(series))]


def run(
    schemes: list[str] | None = None,
    start: dt.date | None = None,
    end: dt.date | None = None,
    max_weight: float = 0.5,
    min_weight: float = 0.05,
    risk_aversion: float = 1.0,
) -> dict[str, Any]:
    schemes = schemes or ["120503", "120504", "120505"]
    start = start or dt.date(2022, 1, 1)
    end = end or dt.date(2024, 12, 31)

    loader = AmfiNavLoader()
    nav_dict = {}
    for s in schemes:
        points = loader.load(s, start=start, end=end)
        nav_dict[s] = points

    aligned = _align_navs(nav_dict, start, end)
    if len(aligned) < 2:
        raise ValueError("Need at least 2 schemes with overlapping data")

    # Compute returns
    returns_dict = {s: _returns(series) for s, series in aligned.items()}
    min_len = min(len(v) for v in returns_dict.values())
    for s in returns_dict:
        returns_dict[s] = returns_dict[s][-min_len:]

    n = len(schemes)
    returns_matrix = np.array([returns_dict[s] for s in schemes])  # n x T

    # Expected returns and covariance
    mu = np.mean(returns_matrix, axis=1) * 252  # annualize
    cov = np.cov(returns_matrix) * 252

    # Mean-variance optimization: maximize w^T mu - (risk_aversion/2) w^T cov w
    # subject to sum(w) = 1, min_weight <= w_i <= max_weight

    def objective(w):
        return -(w @ mu) + (risk_aversion / 2) * (w @ cov @ w)

    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    bounds = [(min_weight, max_weight) for _ in range(n)]

    x0 = np.ones(n) / n
    result = minimize(objective, x0, bounds=bounds, constraints=constraints, method="SLSQP")

    weights = result.x if result.success else x0
    port_return = weights @ mu
    port_vol = np.sqrt(weights @ cov @ weights)
    port_sharpe = port_return / port_vol if port_vol > 0 else 0.0

    allocation = {s: float(w) for s, w in zip(schemes, weights)}

    return {
        "config": {
            "schemes": schemes,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "max_weight": max_weight,
            "min_weight": min_weight,
            "risk_aversion": risk_aversion,
        },
        "expected_annual_returns": {s: float(m) for s, m in zip(schemes, mu)},
        "covariance": cov.tolist(),
        "optimal_weights": allocation,
        "portfolio": {
            "expected_return_annual": float(port_return),
            "volatility_annual": float(port_vol),
            "sharpe": float(port_sharpe),
        },
        "optimization_success": bool(result.success),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--schemes", type=lambda s: s.split(","), default=["120503", "120504", "120505"])
    parser.add_argument("--start", type=lambda s: dt.date.fromisoformat(s), default=None)
    parser.add_argument("--end", type=lambda s: dt.date.fromisoformat(s), default=None)
    parser.add_argument("--max-weight", type=float, default=0.5)
    parser.add_argument("--min-weight", type=float, default=0.05)
    parser.add_argument("--risk-aversion", type=float, default=1.0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(
        schemes=args.schemes,
        start=args.start,
        end=args.end,
        max_weight=args.max_weight,
        min_weight=args.min_weight,
        risk_aversion=args.risk_aversion,
    )

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())