"""mutual_funds/01_fetch_nav: Load and explore AMFI NAV history.

Uses `honba_examples.amfi.AmfiNavLoader` to read NAV series for a scheme from the
local Parquet store, resample to daily bars, and surface missing days. Teaches the
NAV-as-bar model (`open=high=low=close=nav`, zero volume) the other mutual-fund
examples build on. There is no `Strategy` here — runs are offline and deterministic
on recorded NAV fixtures.

Run::

    python mutual_funds/01_fetch_nav.py --scheme 120503 --start 2023-01-01 --end 2024-12-31
    python mutual_funds/01_fetch_nav.py --scheme 120503 --latest
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

from honba_examples.amfi import AmfiNavLoader
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
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--scheme", type=str, default=None, help="AMFI scheme code (e.g., 120503)")
    parser.add_argument("--category", type=str, default=None, help="AMFI category name")
    parser.add_argument("--start", type=lambda s: dt.date.fromisoformat(s), default=None)
    parser.add_argument("--end", type=lambda s: dt.date.fromisoformat(s), default=None)
    parser.add_argument("--latest", action="store_true", help="Fetch only latest NAV")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(
        scheme=args.scheme,
        category=args.category,
        start=args.start,
        end=args.end,
        latest=args.latest,
    )

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())