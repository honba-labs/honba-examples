"""Shared optimizer utilities for the Honba examples."""

from __future__ import annotations

__all__ = [
    "ParetoPoint",
    "extract_pareto_frontier",
    "plot_pareto",
    "sharpe_sortino_dd",
]

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ParetoPoint:
    """A point on the Pareto frontier (return vs drawdown)."""

    fast: int
    slow: int
    return_pct: float
    max_dd_pct: float
    sharpe: float
    sortino: float | None = None
    profit_factor: float | None = None
    n_trades: int = 0


def extract_pareto_frontier(
    results: list[dict[str, Any]],
    return_key: str = "return_pct",
    dd_key: str = "max_dd_pct",
) -> list[ParetoPoint]:
    """Extract the Pareto-optimal frontier (max return for each drawdown level).

    Args:
        results: List of backtest result dicts with return and drawdown keys.
        return_key: Key for return percentage.
        dd_key: Key for max drawdown percentage.

    Returns:
        List of ParetoPoint objects sorted by drawdown.
    """
    # Filter valid results
    valid = [
        r
        for r in results
        if r.get("status") == "ok" and r.get(return_key) is not None and r.get(dd_key) is not None
    ]

    # Sort by drawdown ascending, then return descending
    valid.sort(key=lambda r: (r[dd_key], -r[return_key]))

    pareto: list[ParetoPoint] = []
    best_return = -float("inf")

    for r in valid:
        ret = r[return_key]
        dd = r[dd_key]
        if ret > best_return:
            pareto.append(
                ParetoPoint(
                    fast=r.get("fast", 0),
                    slow=r.get("slow", 0),
                    return_pct=ret,
                    max_dd_pct=dd,
                    sharpe=r.get("sharpe", 0.0),
                    sortino=r.get("sortino"),
                    profit_factor=r.get("profit_factor"),
                    n_trades=r.get("n_trades", 0),
                )
            )
            best_return = ret

    return pareto


def sharpe_sortino_dd(
    sharpe: float,
    sortino: float,
    profit_factor: float,
    max_drawdown: float,
) -> float:
    """Single-objective fitness combining Sharpe, Sortino, Profit Factor, and Drawdown.

    Weights: Sharpe 50%, Sortino 30%, Profit Factor 15%, inverse DD 5%.
    """
    return (
        (sharpe * 0.5)
        + (sortino * 0.3)
        + (max(profit_factor, 1.0) * 0.15)
        + ((1.0 / (1.0 + abs(max_drawdown))) * 0.05)
    )


def plot_pareto(
    pareto: list[ParetoPoint],
    all_points: list[ParetoPoint] | None = None,
    title: str = "Pareto Frontier: Return vs Max Drawdown",
) -> str:
    """Generate a simple ASCII plot of the Pareto frontier.

    Returns the plot as a string.
    """
    lines = [title, ""]

    if not pareto:
        lines.append("No Pareto points to plot.")
        return "\n".join(lines)

    # Find bounds
    all_returns = [p.return_pct for p in pareto]
    all_dds = [p.max_dd_pct for p in pareto]

    if all_points:
        all_returns.extend(p.return_pct for p in all_points)
        all_dds.extend(p.max_dd_pct for p in all_points)

    min_ret, max_ret = min(all_returns), max(all_returns)
    min_dd, max_dd = min(all_dds), max(all_dds)

    # Plot dimensions
    width, height = 60, 20

    # Create canvas
    canvas = [[" " for _ in range(width)] for _ in range(height)]

    # Scale functions
    def x_scale(dd: float) -> int:
        if max_dd == min_dd:
            return width // 2
        return int((dd - min_dd) / (max_dd - min_dd) * (width - 1))

    def y_scale(ret: float) -> int:
        if max_ret == min_ret:
            return height // 2
        return height - 1 - int((ret - min_ret) / (max_ret - min_ret) * (height - 1))

    # Plot all points as dots
    if all_points:
        for p in all_points:
            x, y = x_scale(p.max_dd_pct), y_scale(p.return_pct)
            if 0 <= x < width and 0 <= y < height:
                canvas[y][x] = "."

    # Plot Pareto points as stars
    for p in pareto:
        x, y = x_scale(p.max_dd_pct), y_scale(p.return_pct)
        if 0 <= x < width and 0 <= y < height:
            canvas[y][x] = "*"

    # Connect Pareto points
    for i in range(len(pareto) - 1):
        p1, p2 = pareto[i], pareto[i + 1]
        x1, y1 = x_scale(p1.max_dd_pct), y_scale(p1.return_pct)
        x2, y2 = x_scale(p2.max_dd_pct), y_scale(p2.return_pct)

        # Simple line drawing (Bresenham-ish)
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        steps = max(dx, dy)
        if steps > 0:
            for step in range(steps + 1):
                t = step / steps
                x = int(x1 + t * (x2 - x1))
                y = int(y1 + t * (y2 - y1))
                if 0 <= x < width and 0 <= y < height and canvas[y][x] == " ":
                    canvas[y][x] = "-"

    # Render
    for row in canvas:
        lines.append("".join(row))

    # Axis labels
    lines.append(f"Drawdown: {min_dd:.1f}% ... {max_dd:.1f}%")
    lines.append(f"Return:   {min_ret:.1f}% ... {max_ret:.1f}%")
    lines.append("* = Pareto optimal, . = all evaluated, - = frontier")

    return "\n".join(lines)
