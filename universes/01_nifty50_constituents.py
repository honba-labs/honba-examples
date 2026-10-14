"""01_nifty50_constituents: resolve a static named universe and print its members.

Universe resolution, no strategy: ``resolve_universe()`` is the single entry-point
for any named universe and returns a ``list[InstrumentId]``. Runs offline and
deterministically against the universe map bundled with Honba — no adapter, no
network.

Run::

    python universes/01_nifty50_constituents.py

Pitfalls::

    - Today's list only: resolve_universe("nifty50") without as_of returns a static tuple of
      current members. Its own module docstring calls that survivorship-biased by construction,
      because every name that was dropped or delisted is already missing. Never use this call to
      stand in for a past date; use as_of= with a registered history, which raises when none exists.
    - No effective date: the tuple carries no stamp of when it was captured, and index membership
      changes at each reconstitution, so the printed table is only right on some unknown day.
    - Membership is not data: 50 printed symbols do not mean the catalog has bars for all of them.
      Check coverage before a backtest (07 reports members with no bars).
    - exchange is just a label stamped on each symbol; nothing checks the symbol trades there, and
      a ticker changed by a rename or merger has a different symbol in older bars.
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
        [
            Column("symbol", "Symbol", style="cyan"),
            Column("exchange", "Exchange", style="magenta"),
        ],
        title=f"{UNIVERSE_NAME.upper()} Constituents ({len(members)} symbols)",
    )


if __name__ == "__main__":
    main()
