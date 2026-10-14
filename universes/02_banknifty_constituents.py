"""02_banknifty_constituents: the same resolution for a sector / thematic universe.

Universe resolution, no strategy: Bank Nifty goes through the identical
``resolve_universe()`` call — only the name changes, so the pattern works for any
named universe registered with Honba. Runs offline and deterministically against
the bundled universe map.

Run::

    python universes/02_banknifty_constituents.py

Pitfalls::

    - As bundled this exits with ValueError: UNIVERSES holds only nifty50 and nifty200_alpha30, so
      resolve_universe("banknifty") raises "unknown universe". The example works only once a Bank
      Nifty universe is registered, so the "offline and deterministic" claim above holds for the
      universes that exist, not for this name.
    - A registered sector list is still today's membership, survivorship-biased for any earlier
      date; for a past date it needs a registered history and as_of=, not the static tuple.
    - A sector index is a small, highly correlated set of names, so an equal-weight result over it
      is mostly one sector bet, not a diversified test of a strategy.
    - Membership is not data coverage: confirm the catalog has bars for every member before relying
      on a backtest of it.
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
        [
            Column("symbol", "Symbol", style="cyan"),
            Column("exchange", "Exchange", style="magenta"),
        ],
        title=f"{UNIVERSE_NAME.upper()} Constituents ({len(members)} symbols)",
    )


if __name__ == "__main__":
    main()
