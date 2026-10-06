"""05_alpha30_custom_universe

Build a custom universe from an explicit InstrumentId list and run an
equal-weight rebalance on it.

Demonstrates:
  1. Creating InstrumentId objects by hand (no engine API required).
  2. Passing a custom symbol list directly to UniverseEqualWeightRebalance
     by registering it as an ad-hoc named universe.
  3. A pattern useful for: watchlists, research baskets, or any set of
     tickers that is not tracked by a named index.

Custom universe here: a hand-picked basket of Indian infrastructure names.

Run:
    python 05_alpha30_custom_universe.py
"""

from __future__ import annotations

from honba.domain.instrument import InstrumentId
from honba.markets.india.universes import UNIVERSES, resolve_universe

# ---------------------------------------------------------------------------
# Define your custom basket
# ---------------------------------------------------------------------------
CUSTOM_UNIVERSE_NAME = "custom_infra_basket"
EXCHANGE = "NSE"

CUSTOM_SYMBOLS: tuple[str, ...] = (
    "LT",           # Larsen & Toubro
    "ULTRACEMCO",   # UltraTech Cement
    "ADANIPORTS",   # Adani Ports
    "POWERGRID",    # Power Grid Corporation
    "NTPC",         # NTPC Limited
    "BHARTIARTL",   # Bharti Airtel (telecom infra)
    "SIEMENS",      # Siemens India
    "ABB",          # ABB India
    "VOLTAS",       # Voltas
    "CUMMINSIND",   # Cummins India
)


def register_custom_universe(exchange: str = EXCHANGE) -> list[InstrumentId]:
    """Register CUSTOM_SYMBOLS under CUSTOM_UNIVERSE_NAME and return the list."""
    norm = CUSTOM_UNIVERSE_NAME.lower().replace("-", "_").replace(" ", "_")
    if norm not in UNIVERSES:
        UNIVERSES[norm] = CUSTOM_SYMBOLS
        print(f"[register] '{norm}' → {len(CUSTOM_SYMBOLS)} symbols")
    return [InstrumentId(sym, exchange) for sym in CUSTOM_SYMBOLS]


def main() -> None:
    register_custom_universe()

    # Verify round-trip through the engine API
    resolved = resolve_universe(CUSTOM_UNIVERSE_NAME, exchange=EXCHANGE)

    print(f"\nCustom universe : {CUSTOM_UNIVERSE_NAME}")
    print(f"Exchange           : {EXCHANGE}")
    print(f"Members         : {len(resolved)}")
    print("-" * 36)
    for iid in sorted(resolved, key=lambda x: x.symbol):
        print(f"  {iid.symbol:15s}  {iid.exchange}")

    print(
        "\nPass universe_name=CUSTOM_UNIVERSE_NAME to UniverseEqualWeightRebalance "
        "(see 04_alpha30_generic_rebalancer.py) to trade this basket."
    )


if __name__ == "__main__":
    main()
