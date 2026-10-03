"""02_banknifty_constituents

Resolve the Bank Nifty universe and print its members.

Demonstrates:
  1. Using resolve_universe() for a sector / thematic universe.
  2. The same pattern works for any named universe registered with Honba.

Run:
    python 02_banknifty_constituents.py
"""

from __future__ import annotations

from honba.domain.instrument import InstrumentId
from honba.markets.india.universes import resolve_universe

UNIVERSE_NAME = "banknifty"
VENUE = "NSE"


def main() -> None:
    members: list[InstrumentId] = resolve_universe(UNIVERSE_NAME, venue=VENUE)
    print(f"Universe : {UNIVERSE_NAME}")
    print(f"Venue    : {VENUE}")
    print(f"Members  : {len(members)}")
    print("-" * 36)
    for iid in sorted(members, key=lambda x: x.symbol):
        print(f"  {iid.symbol:15s}  {iid.venue}")


if __name__ == "__main__":
    main()
