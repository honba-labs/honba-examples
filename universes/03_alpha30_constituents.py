"""03_alpha30_constituents: resolve Nifty200 Alpha 30, with a seed-list fallback.

Universe resolution, no strategy. Resolve the current membership through the Honba
universe API and print it; when the engine does not know the name yet, fall back to
a documented seed list while the membership provider is wired up. ``load_alpha30()``
is the preferred helper for strategies that need the universe. Offline and
deterministic.

Catalog counterpart: honba.markets.india.universes

Run::

    python universes/03_alpha30_constituents.py
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
        [Column("symbol", "Symbol"), Column("exchange", "Exchange")],
    )


def main() -> None:
    print_members(load_alpha30())


if __name__ == "__main__":
    main()
