"""mutual_funds/03_category_analysis: Category-level stats and ranking.

Loads the schemes of an AMFI category, computes rolling CAGR, drawdown and Sharpe
metrics, and buckets schemes into quartiles. Teaches batch NAV loading, metric
computation and universe ranking. No `Strategy` subclass — runs are offline and
deterministic on recorded NAV fixtures.

Run::

    python mutual_funds/03_category_analysis.py --category "Equity:Large Cap" --start 2020-01-01
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
from pathlib import Path
from typing import Any

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba_examples.amfi import AmfiNavLoader
from honba_examples.metrics import max_drawdown, sharpe_ratio

__all__ = ["main", "run"]


def _annual_returns(nav_points: list, years: int) -> list[float]:
    if len(nav_points) < 2:
        return []
    first, last = nav_points[0], nav_points[-1]
    days = (last.date - first.date).days
    if days < 30:
        return []
    return [((last.nav / first.nav) ** (365.25 / days)) - 1]


def _rolling_cagr(nav_points: list, window_days: int = 252) -> list[float]:
    if len(nav_points) < window_days + 1:
        return []
    results = []
    for i in range(window_days, len(nav_points)):
        start = nav_points[i - window_days]
        end = nav_points[i]
        days = (end.date - start.date).days
        if days > 30:
            cagr_val = (end.nav / start.nav) ** (365.25 / days) - 1
            results.append(cagr_val)
    return results


def run(
    category: str = "Equity:Large Cap",
    start: dt.date | None = None,
    end: dt.date | None = None,
    min_history_days: int = 500,
) -> dict[str, Any]:
    start = start or dt.date(2020, 1, 1)
    end = end or dt.date(2024, 12, 31)

    loader = AmfiNavLoader()
    all_data = loader.load_category(category, start=start, end=end)

    results = []
    for scheme_code, nav_points in all_data.items():
        if len(nav_points) < min_history_days:
            continue

        nav_points.sort(key=lambda p: p.date)
        rets = _rolling_cagr(nav_points)

        if not rets:
            continue

        # Compute metrics
        cagr_1y = rets[-1] if rets else 0.0
        cagr_3y = _annual_returns(nav_points, 3)[0] if len(nav_points) > 750 else 0.0
        cagr_5y = _annual_returns(nav_points, 5)[0] if len(nav_points) > 1250 else 0.0

        # Convert nav_points to format for metrics
        curve = [{"date": p.date.isoformat(), "value": p.nav, "cash": 0} for p in nav_points]
        mdd = max_drawdown(curve) if len(curve) > 1 else 0.0
        sharpe = sharpe_ratio(curve) if len(curve) > 2 else 0.0

        results.append({
            "scheme_code": scheme_code,
            "scheme_name": nav_points[0].scheme_name if nav_points else "",
            "nav_count": len(nav_points),
            "cagr_1y": cagr_1y,
            "cagr_3y": cagr_3y,
            "cagr_5y": cagr_5y,
            "max_drawdown_pct": mdd,
            "sharpe": sharpe,
            "mean_rolling_cagr": statistics.mean(rets) if rets else 0.0,
            "std_rolling_cagr": statistics.stdev(rets) if len(rets) > 1 else 0.0,
        })

    # Rank by 3y CAGR, then Sharpe
    results.sort(key=lambda x: (-x["cagr_3y"], -x["sharpe"]))

    # Quartile buckets by 3y CAGR
    if results:
        n = len(results)
        q1 = n // 4
        q2 = n // 2
        q3 = 3 * n // 4
        for i, r in enumerate(results):
            if i < q1:
                r["quartile"] = "Q1 (top)"
            elif i < q2:
                r["quartile"] = "Q2"
            elif i < q3:
                r["quartile"] = "Q3"
            else:
                r["quartile"] = "Q4 (bottom)"

    return {
        "config": {"category": category, "start": start.isoformat(), "end": end.isoformat()},
        "total_schemes": len(all_data),
        "analyzed_schemes": len(results),
        "schemes": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--category", type=str, default="Equity:Large Cap")
    parser.add_argument("--start", type=lambda s: dt.date.fromisoformat(s), default=None)
    parser.add_argument("--end", type=lambda s: dt.date.fromisoformat(s), default=None)
    parser.add_argument("--min-history-days", type=int, default=500)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(
        category=args.category,
        start=args.start,
        end=args.end,
        min_history_days=args.min_history_days,
    )

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())