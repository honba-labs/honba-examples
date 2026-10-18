"""mutual_funds/03_category_analysis: Category-level stats and ranking.

Loads the schemes of an AMFI category, computes rolling CAGR, drawdown and Sharpe
metrics, and buckets schemes into quartiles. Teaches batch NAV loading, metric
computation and universe ranking. No `Strategy` subclass — runs are offline and
deterministic on recorded NAV fixtures.

Run::

    python examples/mutual_funds/03_category_analysis.py --category "Equity:Large Cap" --start 2020-01-01

Pitfalls::

    - Empty today: `load_category` returns no schemes, so the report has total_schemes 0 and no
      ranking until a category mapping exists. Read nothing into an empty table.
    - Survivorship and reclassification: category membership is today's mapping applied to the whole
      history. Merged and closed funds are gone, and a scheme that was recategorised (SEBI's 2017
      re-categorisation) is placed in its current category for years it did not belong to, so a top
      quartile is partly survivors. Mixing Direct and Regular plans mixes expense ratios.
    - Short history reads as zero: cagr_3y and cagr_5y are trailing windows and stay 0.0 when a fund
      has less history, which is not a 0 percent return yet sorts like one. cagr_1y is
      the last 252 observations, so it spans more than a year when NAV rows are missing.
    - Backward-looking ranks: quartiles come from trailing 3y CAGR at one end date and do not
      persist. Rolling CAGR windows overlap, so their std is not independent; Sharpe uses daily NAV
      returns with a zero risk-free rate and 252 sessions a year.
"""

from __future__ import annotations

import datetime as dt
import statistics
import sys
from typing import Any

from honba_examples.amfi import AmfiNavLoader
from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.metrics import max_drawdown, sharpe_ratio

__all__ = ["main", "run"]


def _annual_returns(nav_points: list, years: int) -> list[float]:
    """Annualised return over the trailing ``years`` years, or ``[]`` without that much history."""
    if len(nav_points) < 2:
        return []
    last = nav_points[-1]
    cutoff = last.date - dt.timedelta(days=round(years * 365.25))
    if nav_points[0].date > cutoff:
        return []  # history does not reach back far enough: do not label a shorter span "Ny"
    first = [p for p in nav_points if p.date <= cutoff][-1]  # last NAV published on/before cutoff
    days = (last.date - first.date).days
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
        cagr_3y = next(iter(_annual_returns(nav_points, 3)), 0.0)
        cagr_5y = next(iter(_annual_returns(nav_points, 5)), 0.0)

        # Convert nav_points to format for metrics
        curve = [{"date": p.date.isoformat(), "value": p.nav, "cash": 0} for p in nav_points]
        mdd = max_drawdown(curve) if len(curve) > 1 else 0.0
        sharpe = sharpe_ratio(curve, capital=100_000.0) if len(curve) > 2 else 0.0

        results.append(
            {
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
            }
        )

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
    parser = make_parser(__doc__)
    parser.add_argument("--category", type=str, default="Equity:Large Cap")
    parser.add_argument("--start", type=lambda s: dt.date.fromisoformat(s), default=None)
    parser.add_argument("--end", type=lambda s: dt.date.fromisoformat(s), default=None)
    parser.add_argument("--min-history-days", type=int, default=500)
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(
        category=args.category,
        start=args.start,
        end=args.end,
        min_history_days=args.min_history_days,
    )

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
