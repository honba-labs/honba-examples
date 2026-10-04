"""03_alpha30_constituents

Demonstrate how to work with the Nifty200 Alpha 30 universe.

1. Resolve the current membership via the Honba universe API.
2. Fall back to a documented seed list when the engine does not yet
   know the universe (useful while the membership provider is being
   wired up).
3. Print a clean table of InstrumentIds so downstream strategies can
   consume them.

Catalog counterpart: honba.markets.india.universes
"""

from __future__ import annotations

from honba.domain.instrument import InstrumentId
from honba.markets.india.universes import resolve_universe, UNIVERSES

# ---------------------------------------------------------------------------
# Seed list (snapshot of Nifty200 Alpha 30 – replace with live feed later)
# Symbols follow NSE primary listing convention used throughout Honba.
# ---------------------------------------------------------------------------
NIFTY200_ALPHA_30_SEED: tuple[str, ...] = (
    "ADANIPOWER",
    "SHRIRAMFIN",
    "HINDALCO",
    "ADANIGREEN",
    "EICHERMOT",
    "ADANIENSOL",
    "IDEA",
    "BHEL",
    "CUMMINSIND",
    "POWERINDIA",          # Hitachi Energy India
    "POLYCAB",
    "MUTHOOTFIN",
    "PAYTM",
    "INDIANB",
    "LAURUSLABS",
    "VEDL",
    "BHARATFORG",
    "NYKAA",              # FSN E-Commerce
    "ASHOKLEY",
    "MCX",
    "FEDERALBNK",
    "AUBANK",
    "LTF",
    "SAIL",
    "GLENMARK",
    "FORTIS",
    "NATIONALUM",
    "BSE",
    "ABCAPITAL",
    "DIXON",
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


if __name__ == "__main__":
    members = load_alpha30()
    print(f"Nifty200 Alpha 30 → {len(members)} instruments")
    for iid in sorted(members, key=lambda x: x.symbol):
        print(f"  {iid.symbol:15s}  {iid.exchange}")