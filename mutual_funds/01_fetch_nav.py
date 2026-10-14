"""mutual_funds/01_fetch_nav: Load and explore AMFI NAV history.

Uses `honba_examples.amfi.AmfiNavLoader` to read NAV series for a scheme from the
local Parquet store and print them as stored (no resampling; gaps are not reported).
Teaches the NAV-as-bar model (`open=high=low=close=nav`, zero volume) the other mutual-fund
examples build on. There is no `Strategy` here — runs are offline and deterministic
on recorded NAV fixtures.

Run::

    python mutual_funds/01_fetch_nav.py --scheme 120503 --start 2023-01-01 --end 2024-12-31
    python mutual_funds/01_fetch_nav.py --scheme 120503 --latest

Pitfalls::

    - NAV timing: a NAV is struck at the close and published by AMFI afterwards (later for some
      categories), yet the bar is stamped 09:15 IST of its own date. Acting on nav[D] during day D
      is lookahead; a decision that uses a NAV should only trade at the next NAV that can still be
      bought.
    - Series as stored: nothing is resampled and no missing days are reported. Holidays and
      publication gaps are simply absent, and `latest_nav` returns the last stored NAV however old
      it is, with no staleness check against today.
    - Scheme code is the plan: Direct and Regular plans, and Growth and IDCW options, have separate
      AMFI codes and separate NAVs. An IDCW NAV drops on each payout, so it is not a total-return
      series.
    - Survivorship: only schemes that were imported appear, and merged or wound-up schemes drop out
      of the current NAVAll feed. `--category` is not usable yet, because `load_category` is a stub
      that returns no schemes.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba_examples.amfi import AmfiNavLoader
from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.jsonable import jsonable

__all__ = ["main", "run"]


def run(
    scheme: str | None = None,
    category: str | None = None,
    start: dt.date | None = None,
    end: dt.date | None = None,
    latest: bool = False,
    out: Path | None = None,
) -> dict[str, Any]:
    loader = AmfiNavLoader()

    if latest and scheme:
        nav = loader.latest_nav(scheme)
        return {
            "type": "latest",
            "scheme": scheme,
            "nav_point": jsonable(nav),
        }

    if category:
        data = loader.load_category(category, start=start, end=end)
        return {
            "type": "category",
            "category": category,
            "scheme_count": len(data),
            "schemes": {k: [jsonable(p) for p in v] for k, v in data.items()},
        }

    if scheme:
        points = loader.load(scheme, start=start, end=end)
        return {
            "type": "scheme",
            "scheme": scheme,
            "count": len(points),
            "nav_points": [jsonable(p) for p in points],
        }

    raise ValueError("Provide --scheme or --category")


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--scheme", type=str, default=None, help="AMFI scheme code (e.g., 120503)")
    parser.add_argument("--category", type=str, default=None, help="AMFI category name")
    parser.add_argument("--start", type=lambda s: dt.date.fromisoformat(s), default=None)
    parser.add_argument("--end", type=lambda s: dt.date.fromisoformat(s), default=None)
    parser.add_argument("--latest", action="store_true", help="Fetch only latest NAV")
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(
        scheme=args.scheme,
        category=args.category,
        start=args.start,
        end=args.end,
        latest=args.latest,
    )

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
