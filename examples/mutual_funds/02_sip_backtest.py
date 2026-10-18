"""mutual_funds/02_sip_backtest: SIP simulation with fixed-date monthly investment.

Simulates a Systematic Investment Plan: invest a fixed amount on a fixed calendar
date each month, buy units at that day's NAV (falling back to the next published NAV,
and skipping a SIP with none within a week), and track units, invested amount, current
value and return. Teaches the units-at-NAV accounting behind SIP returns; the XIRR
helper lives in `honba_examples.metrics`. No `Strategy` subclass — runs are offline
and deterministic on recorded NAV fixtures.

Run::

    python examples/mutual_funds/02_sip_backtest.py --scheme 120503 --monthly 10000 --start 2023-01-01

Pitfalls::

    - SIP date versus NAV date: sip_day is a calendar day, and a non-publication day (weekend,
      holiday) is filled at the next published NAV, never an earlier one. An instalment with no NAV
      within 7 days (before the scheme existed, a data gap, past the last NAV) is skipped and listed
      in skipped_sip_dates, so check it before comparing two schemes.
    - Result measure: return_pct is gain over total invested, not annualised and not XIRR, and it is
      valued at the last NAV in the window, so the end date moves it. Use XIRR from
      `honba_examples.metrics` to compare plans with different cash-flow timing.
    - Costs and taxes are absent: exit load, stamp duty on purchase and tax on gains are not
      charged. NAV is already net of the expense ratio, so a Direct and a Regular code of the same
      fund differ by that cost; do not subtract the expense ratio again.
    - Selection and survivorship: one scheme named after the fact, from schemes that exist today, so
      merged and wound-up funds never appear. Run several start dates and SIP days, and report the
      range.
"""

from __future__ import annotations

import calendar
import datetime as dt
import sys
from pathlib import Path
from typing import Any

from honba_examples.amfi import AmfiNavLoader
from honba_examples.cli import add_out_arg, emit_json, make_parser

__all__ = ["main", "run"]

MAX_FILL_DELAY_DAYS = 7
"""Longest wait for the next NAV (weekend plus holidays); beyond it the instalment is skipped."""


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
    data_dir: Path | None = None,
) -> dict[str, Any]:
    start = start or dt.date(2023, 1, 1)
    end = end or dt.date(2024, 12, 31)

    loader = AmfiNavLoader(data_dir)
    nav_points = loader.load(scheme, start=start, end=end)
    nav_by_date = {p.date: p.nav for p in nav_points}

    sip_dates = _sip_dates(start, end, sip_day)
    legs: list[dict[str, Any]] = []
    total_units = 0.0
    total_invested = 0.0

    skipped: list[str] = []
    publication_dates = sorted(nav_by_date)

    for sip_date in sip_dates:
        # An order placed on a non-publication day is processed at the NEXT NAV; the
        # previous day's NAV is already history. A SIP with no NAV within a week (before the scheme
        # existed, a data gap, past the last NAV) is skipped, not filled at a faraway price.
        effective_date = next((d for d in publication_dates if d >= sip_date), None)
        if effective_date is None or (effective_date - sip_date).days > MAX_FILL_DELAY_DAYS:
            skipped.append(sip_date.isoformat())
            continue
        nav = nav_by_date[effective_date]

        units = monthly / nav
        total_units += units
        total_invested += monthly
        legs.append(
            {
                "sip_date": sip_date.isoformat(),
                "effective_date": effective_date.isoformat(),
                "nav": nav,
                "units": units,
                "amount": monthly,
                "total_units": total_units,
                "total_invested": total_invested,
            }
        )

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
        "skipped_sip_dates": skipped,
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
    parser = make_parser(__doc__)
    parser.add_argument("--scheme", type=str, default="120503")
    parser.add_argument("--monthly", type=float, default=10000.0)
    parser.add_argument("--start", type=lambda s: dt.date.fromisoformat(s), default=None)
    parser.add_argument("--end", type=lambda s: dt.date.fromisoformat(s), default=None)
    parser.add_argument("--sip-day", type=int, default=7, help="Calendar day for SIP (1-28)")
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(
        scheme=args.scheme,
        monthly=args.monthly,
        start=args.start,
        end=args.end,
        sip_day=args.sip_day,
    )

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
