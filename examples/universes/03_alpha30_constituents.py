"""03_alpha30_constituents: resolve Nifty200 Alpha 30, with a seed-list fallback.

Universe resolution, no strategy. Resolve the current membership through the Honba
universe API and print it; when the engine does not know the name yet, fall back to
an older seed list (also selectable with ``--list seed``). ``load_alpha30()``
is the preferred helper for strategies that need the universe. Offline and
deterministic.

Catalog counterpart: honba.markets.india.universes

Run::

    python examples/universes/03_alpha30_constituents.py

Pitfalls::

    - Today's members chosen by past returns: Alpha 30 ranks stocks by past alpha, so each name
      is in the list because of returns that already happened. Backtesting today's list over
      that earlier window selects winners with hindsight, on top of ordinary survivorship.
    - The shipped history is not history: default_alpha30_history() in honba stamps every one of
      today's 30 names as included from 2005-04-01, so resolve_universe(as_of=...) returns today's
      list for any date after that. The point-in-time API exists but the data behind it is not
      real reconstitution history; replace it before using as_of.
    - Two lists that disagree: the engine list (EICHERMOT, FORTIS, GLENMARK, MUTHOOTFIN) and the
      older seed (AUROPHARMA, MAHABANK, MOTHERSON, UNIONBANK) are different baskets. Both live in
      honba_examples/universe_lists.py; the engine list is authoritative and the default for 03,
      07 and 08, and --list seed / --alpha30-list seed opt into the seed. Do not mix them when
      comparing results.
    - register_alpha30_if_missing mutates the process-wide UNIVERSES map as a hidden
      side effect (its exchange argument is unused), and load_alpha30 swallows ValueError, so a
      misspelt name falls through to the seed without a warning.
"""

from __future__ import annotations

import argparse

from honba.display import Column, render_table
from honba.domain.instrument import InstrumentId
from honba.markets.india.universes import UNIVERSES, resolve_universe

from honba_examples.display_style import SYMBOL
from honba_examples.universe_lists import (
    ALPHA30_SEED,
    ALPHA30_VARIANTS,
    DEFAULT_ALPHA30_VARIANT,
    alpha30_instruments,
)

# The lists live in honba_examples.universe_lists so 03/05/07/08 share one source. The engine
# list (what resolve_universe returns) is authoritative; the seed is a historical alternative.
NIFTY200_ALPHA_30_SEED: tuple[str, ...] = ALPHA30_SEED

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


def load_alpha30(
    exchange: str = "NSE", variant: str = DEFAULT_ALPHA30_VARIANT
) -> list[InstrumentId]:
    """Preferred entry point for any strategy that needs the universe.

    ``variant="engine"`` (default, authoritative) tries the official engine API first and falls
    back to the seed list only if the engine does not know the name. ``variant="seed"`` returns
    the historical seed list directly, with no registration side effect.
    """
    if variant != "engine":
        return alpha30_instruments(variant, exchange)
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
            Column("symbol", "Symbol", style=SYMBOL),
            Column("exchange", "Exchange", style="magenta"),
        ],
        title=f"Nifty200 Alpha 30 ({len(members)} instruments)",
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve Nifty200 Alpha 30 through the Honba universe API (engine list, "
            "authoritative) or the historical seed list."
        )
    )
    parser.add_argument(
        "--list",
        dest="variant",
        choices=ALPHA30_VARIANTS,
        default=DEFAULT_ALPHA30_VARIANT,
        help="engine = resolve_universe list (default); seed = older alternative snapshot",
    )
    args = parser.parse_args(argv)
    print_members(load_alpha30(variant=args.variant))


if __name__ == "__main__":
    main()
