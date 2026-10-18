"""05_alpha30_custom_universe: register and resolve a hand-built universe.

Universe resolution, no strategy: build ``InstrumentId`` objects from an explicit
symbol tuple without the engine API, register them under an ad-hoc name, and verify
the round-trip through ``resolve_universe()``. Pass ``CUSTOM_UNIVERSE_NAME`` to
``UniverseEqualWeightRebalance`` (04) to trade the basket. Useful for watchlists and
research baskets that no named index tracks. Runs offline and deterministically.

Custom universe here: a hand-picked basket of Indian infrastructure names.

Run::

    python universes/05_alpha30_custom_universe.py

Pitfalls::

    - A basket picked today with hindsight: ten large names that are all still listed (and
      "infrastructure" is loose: the list includes a telecom operator). Backtesting
      it over a past window is survivorship plus selection bias. Write down when and why you
      would have chosen it, or register a UniverseHistory.
    - Static registration only: the tuple goes into the process-wide UNIVERSES map, so
      resolve_universe(as_of=...) for this name raises (no history), and registering twice under a
      name that already holds other symbols silently keeps the old ones.
    - No existence check: InstrumentId objects are built from strings; a typo or a renamed ticker
      only shows up later as missing bars, and the exchange is a label, not a verified listing.
    - register_custom_universe returns CUSTOM_SYMBOLS, not what the registry holds, so the two
      can differ if the name was already registered.
"""

from __future__ import annotations

from honba.display import Column, render_kv, render_table
from honba.domain.instrument import InstrumentId
from honba.markets.india.universes import UNIVERSES, resolve_universe

# ---------------------------------------------------------------------------
# Define your custom basket
# ---------------------------------------------------------------------------
CUSTOM_UNIVERSE_NAME = "custom_infra_basket"
EXCHANGE = "NSE"

CUSTOM_SYMBOLS: tuple[str, ...] = (
    "LT",  # Larsen & Toubro
    "ULTRACEMCO",  # UltraTech Cement
    "ADANIPORTS",  # Adani Ports
    "POWERGRID",  # Power Grid Corporation
    "NTPC",  # NTPC Limited
    "BHARTIARTL",  # Bharti Airtel (telecom infra)
    "SIEMENS",  # Siemens India
    "ABB",  # ABB India
    "VOLTAS",  # Voltas
    "CUMMINSIND",  # Cummins India
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

    render_kv(
        [
            ("Custom universe", CUSTOM_UNIVERSE_NAME),
            ("Exchange", EXCHANGE),
            ("Members", len(resolved)),
        ]
    )
    render_table(
        [
            {"symbol": iid.symbol, "exchange": iid.exchange}
            for iid in sorted(resolved, key=lambda x: x.symbol)
        ],
        [
            Column("symbol", "Symbol", style="cyan"),
            Column("exchange", "Exchange", style="magenta"),
        ],
        title=f"{CUSTOM_UNIVERSE_NAME.upper()} ({len(resolved)} symbols)",
    )
    print(
        "\nPass universe_name=CUSTOM_UNIVERSE_NAME to UniverseEqualWeightRebalance "
        "(see 04_alpha30_generic_rebalancer.py) to trade this basket."
    )


if __name__ == "__main__":
    main()
