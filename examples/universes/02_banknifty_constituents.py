"""02_banknifty_constituents: the same resolution for a sector / thematic universe.

Universe resolution, no strategy: Bank Nifty goes through the identical
``resolve_universe()`` call — only the name changes, so the pattern works for any
named universe registered with Honba. Bank Nifty is not registered in the core and no
member list ships in this workspace, so the script reports that and exits unless a
catalog history exists (see Pitfalls).

Run::

    python universes/02_banknifty_constituents.py

Pitfalls::

    - As bundled this exits with an error message: the core registers only nifty50 and
      nifty200_alpha30 and the workspace has no Bank Nifty constituent file, so there is no
      reliable member list to print. The script checks for a catalog history at
      <data root>/constituents/banknifty.parquet and otherwise stops rather than guess members.
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

from honba_examples.universe_lists import UniverseUnavailable, banknifty_members

UNIVERSE_NAME = "banknifty"
EXCHANGE = "NSE"


def main() -> None:
    try:
        members: list[InstrumentId] = banknifty_members(EXCHANGE)
    except UniverseUnavailable as exc:
        raise SystemExit(f"error: {exc}") from None
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
