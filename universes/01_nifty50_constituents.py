"""01_nifty50_constituents: resolve a static named universe and print its members.

Universe resolution, no strategy: ``resolve_universe()`` is the single entry-point
for any named universe and returns a ``list[InstrumentId]``. Runs offline and
deterministically against the universe map bundled with Honba — no adapter, no
network.

Run::

    python universes/01_nifty50_constituents.py
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
