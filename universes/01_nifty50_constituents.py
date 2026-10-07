"""01_nifty50_constituents

Resolve the Nifty 50 universe and print its members.

Demonstrates:
  1. resolve_universe() – the single entry-point for any named universe.
  2. Iterating InstrumentId objects returned by the API.

Run:
    python 01_nifty50_constituents.py
"""

from __future__ import annotations

from honba.display import Column, render_kv, render_table
from honba.domain.instrument import InstrumentId
from honba.markets.india.universes import resolve_universe

UNIVERSE_NAME = "nifty50"
EXCHANGE = "NSE"


def main() -> None:
    members: list[InstrumentId] = resolve_universe(UNIVERSE_NAME, exchange=EXCHANGE)
    render_kv([("Universe", UNIVERSE_NAME), ("Exchange", EXCHANGE), ("Members", len(members))])
    render_table(
        [
            {"symbol": iid.symbol, "exchange": iid.exchange}
            for iid in sorted(members, key=lambda x: x.symbol)
        ],
        [Column("symbol", "Symbol"), Column("exchange", "Exchange")],
    )


if __name__ == "__main__":
    main()
