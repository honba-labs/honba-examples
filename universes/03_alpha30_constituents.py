"""03_alpha30_constituents: resolve Nifty200 Alpha 30, with a seed-list fallback.

Universe resolution, no strategy. Resolve the current membership through the Honba
universe API and print it; when the engine does not know the name yet, fall back to
a documented seed list while the membership provider is wired up. ``load_alpha30()``
is the preferred helper for strategies that need the universe. Offline and
deterministic.

Catalog counterpart: honba.markets.india.universes

Run::

    python universes/03_alpha30_constituents.py

Pitfalls::

    - Today's members chosen by past returns: Alpha 30 ranks stocks by past alpha, so each name
      is in the list because of returns that already happened. Backtesting today's list over
      that earlier window selects winners with hindsight, on top of ordinary survivorship.
    - The shipped history is not history: default_alpha30_history() in honba stamps every one of
      today's 30 names as included from 2005-04-01, so resolve_universe(as_of=...) returns today's
      list for any date after that. The point-in-time API exists but the data behind it is not
      real reconstitution history; replace it before using as_of.
    - Two lists that disagree: load_alpha30() returns the engine list when it is non-empty and the
      seed here only as a fallback, and the two differ (the engine has EICHERMOT, FORTIS, GLENMARK
      and MUTHOOTFIN where the seed has AUROPHARMA, MAHABANK, MOTHERSON and UNIONBANK). 08 uses a
      hard-coded copy of the seed, 07 and 04 to 06 use the engine, so results are not like for like.
    - register_alpha30_if_missing mutates the process-wide UNIVERSES map as a hidden
      side effect (its exchange argument is unused), and load_alpha30 swallows ValueError, so a
      misspelt name falls through to the seed without a warning.
"""

from __future__ import annotations

from honba.display import Column, render_table
from honba.domain.instrument import InstrumentId
from honba.markets.india.universes import UNIVERSES, resolve_universe

# ---------------------------------------------------------------------------
# Seed list (canonical Nifty200 Alpha 30 Union on NSE)
# Symbols follow NSE primary listing convention used throughout Honba.
# ---------------------------------------------------------------------------
NIFTY200_ALPHA_30_SEED: tuple[str, ...] = (
    "ABCAPITAL",
    "ADANIENSOL",
    "ADANIGREEN",
    "ADANIPOWER",
    "ASHOKLEY",
    "AUBANK",
    "AUROPHARMA",
    "BHARATFORG",
    "BHEL",
    "BSE",
    "CUMMINSIND",
    "FEDERALBNK",
    "GVT&D",
    "HINDALCO",
    "IDEA",
    "INDIANB",
    "LAURUSLABS",
    "LTF",
    "MAHABANK",
    "MCX",
    "MOTHERSON",
    "NATIONALUM",
    "NYKAA",
    "PAYTM",
    "POLYCAB",
    "POWERINDIA",
    "SAIL",
    "SHRIRAMFIN",
    "UNIONBANK",
    "VEDL",
)

UNIVERSE_NAME = "nifty200_alpha_30"


def register_alpha30_if_missing(exchange: str = "NSE") -> None:
    """Idempotently add the Alpha-30 seed into the global UNIVERSES map.

    Call this once at process start when the membership provider is not
    yet live.  Real historical reconstitutions should replace the seed.
    """
    norm = UNIVERSE_NAME.lower().replace("-", "_").replace(" ", "_")
    if norm not in UNIVERSES:
        UNIVERSES[norm] = NIFTY200_ALPHA_30_SEED
        print(f"[register] added {norm} ({len(NIFTY200_ALPHA_30_SEED)} names)")


def load_alpha30(exchange: str = "NSE") -> list[InstrumentId]:
    """Preferred entry point for any strategy that needs the universe.

    Tries the official engine API first; falls back to the seed list.
    """
    try:
        members = resolve_universe(UNIVERSE_NAME, exchange=exchange)
        if members:
            return members
    except ValueError:
        pass

    # Fallback path used while the engine is still learning the universe
    register_alpha30_if_missing(exchange)
    return [InstrumentId(sym, exchange) for sym in NIFTY200_ALPHA_30_SEED]


def print_members(members: list[InstrumentId]) -> None:
    """Print the members as a Symbol / Exchange table."""
    print(f"Nifty200 Alpha 30 → {len(members)} instruments")
    render_table(
        [
            {"symbol": iid.symbol, "exchange": iid.exchange}
            for iid in sorted(members, key=lambda x: x.symbol)
        ],
        [
            Column("symbol", "Symbol", style="cyan"),
            Column("exchange", "Exchange", style="magenta"),
        ],
        title=f"Nifty200 Alpha 30 ({len(members)} instruments)",
    )


def main() -> None:
    print_members(load_alpha30())


if __name__ == "__main__":
    main()
