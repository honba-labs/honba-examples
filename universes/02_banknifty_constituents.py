"""02_banknifty_constituents: the same resolution for a sector / thematic universe.

Universe resolution, no strategy: Bank Nifty goes through the identical
``resolve_universe()`` call — only the name changes, so the pattern works for any
named universe registered with Honba. Runs offline and deterministically against
the bundled universe map.

Run::

    python universes/02_banknifty_constituents.py
"""

from __future__ import annotations

from honba.display import Column, render_kv, render_table
from honba.domain.instrument import InstrumentId
from honba.markets.india.universes import resolve_universe

UNIVERSE_NAME = "banknifty"
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
