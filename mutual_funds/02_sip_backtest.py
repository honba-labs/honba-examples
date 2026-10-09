"""mutual_funds/02_sip_backtest: SIP simulation with fixed-date monthly investment.

Simulates a Systematic Investment Plan: invest a fixed amount on a fixed calendar
date each month, buy units at that day's NAV (falling back to the nearest trading
day), and track units, invested amount, current value and return. Teaches the
units-at-NAV accounting behind SIP returns; the XIRR helper lives in
`honba_examples.metrics`. No `Strategy` subclass — runs are offline and
deterministic on recorded NAV fixtures.

Run::

    python mutual_funds/02_sip_backtest.py --scheme 120503 --monthly 10000 --start 2023-01-01
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba_examples.amfi import AmfiNavLoader

__all__ = ["main", "run"]


def _sip_dates(start: dt.date, end: dt.date, day_of_month: int) -> list[dt.date]:
    dates: list[dt.date] = []
    year, month = start.year, start.month
    end_ym = (end.year, end.month)
    while (year, month) <= end_ym:
        last = calendar.monthrange(year, month)[1]
        d = min(day_of_month, last)
        date = dt.date(year, month, d)
        if start <= date <= end:
            dates.append(date)
        month += 1
        if month > 12:
            month = 1
            year += 1
    return dates


def run(
    scheme: str = "120503",
    monthly: float = 10000.0,
    start: dt.date | None = None,
    end: dt.date | None = None,
    sip_day: int = 7,
) -> dict[str, Any]:
    start = start or dt.date(2023, 1, 1)
    end = end or dt.date(2024, 12, 31)

    loader = AmfiNavLoader()
    nav_points = loader.load(scheme, start=start, end=end)
    nav_by_date = {p.date: p.nav for p in nav_points}

    sip_dates = _sip_dates(start, end, sip_day)
    legs: list[dict[str, Any]] = []
    total_units = 0.0
    total_invested = 0.0

    for sip_date in sip_dates:
        nav = nav_by_date.get(sip_date)
        if nav is None:
            nearest = min(nav_by_date, key=lambda d: abs((d - sip_date).days))
            nav = nav_by_date[nearest]
            effective_date = nearest
        else:
            effective_date = sip_date

        units = monthly / nav
        total_units += units
        total_invested += monthly
        legs.append({
            "sip_date": sip_date.isoformat(),
            "effective_date": effective_date.isoformat(),
            "nav": nav,
            "units": units,
            "amount": monthly,
            "total_units": total_units,
            "total_invested": total_invested,
        })

    final_nav = nav_points[-1].nav if nav_points else 0.0
    current_value = total_units * final_nav
    gain = current_value - total_invested
    return_pct = (gain / total_invested * 100) if total_invested else 0.0

    return {
        "config": {
            "scheme": scheme,
            "monthly": monthly,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "sip_day": sip_day,
        },
        "legs": legs,
        "summary": {
            "total_invested": total_invested,
            "total_units": total_units,
            "final_nav": final_nav,
            "current_value": current_value,
            "gain": gain,
            "return_pct": return_pct,
            "sip_count": len(legs),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--scheme", type=str, default="120503")
    parser.add_argument("--monthly", type=float, default=10000.0)
    parser.add_argument("--start", type=lambda s: dt.date.fromisoformat(s), default=None)
    parser.add_argument("--end", type=lambda s: dt.date.fromisoformat(s), default=None)
    parser.add_argument("--sip-day", type=int, default=7, help="Calendar day for SIP (1-28)")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(
        scheme=args.scheme,
        monthly=args.monthly,
        start=args.start,
        end=args.end,
        sip_day=args.sip_day,
    )

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())