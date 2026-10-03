"""01_nifty50_constituents

Resolve the Nifty 50 universe and print its members.

Demonstrates:
  1. resolve_universe() – the single entry-point for any named universe.
  2. Iterating InstrumentId objects returned by the API.

Run:
    python 01_nifty50_constituents.py
"""

from __future__ import annotations

from honba.domain.instrument import InstrumentId
from honba.markets.india.universes import resolve_universe

UNIVERSE_NAME = "nifty50"
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
