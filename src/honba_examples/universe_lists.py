"""Universe member lists shared by the ``universes/`` examples.

One place for the Nifty200 Alpha 30 lists so 03, 05, 07 and 08 cannot drift apart again.

Authoritative list: ``ALPHA30_ENGINE`` is what ``resolve_universe("nifty200_alpha_30")`` returns
in the Honba core, and it is the default everywhere (07 trades it, so 08 must too to be
comparable). ``ALPHA30_SEED`` is an older, undated seed list kept only as an alternative
snapshot, selectable with ``variant="seed"`` / ``--alpha30-list seed``. Neither is point-in-time
history: both are the current names, and the core's shipped history stamps them from 2005-04-01.

Bank Nifty: the workspace holds no Bank Nifty constituent file (the core registers only nifty50
and nifty200_alpha30), so ``banknifty_members`` resolves it from a catalog history if one exists
and otherwise raises ``UniverseUnavailable`` with a clear message. It never invents a list.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from honba.domain.instrument import InstrumentId
from honba.markets.india.universes import NIFTY_200_ALPHA_30_SYMBOLS, resolve_universe

ALPHA30_ENGINE: tuple[str, ...] = tuple(NIFTY_200_ALPHA_30_SYMBOLS)

ALPHA30_SEED: tuple[str, ...] = (
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

ALPHA30_VARIANTS: tuple[str, ...] = ("engine", "seed")
DEFAULT_ALPHA30_VARIANT = "engine"


class UniverseUnavailable(LookupError):
    """A universe has no reliable member list in this workspace."""


def alpha30_symbols(variant: str = DEFAULT_ALPHA30_VARIANT) -> tuple[str, ...]:
    """Alpha 30 symbols for ``variant`` ("engine" is authoritative, "seed" is historical)."""
    if variant == "engine":
        return ALPHA30_ENGINE
    if variant == "seed":
        return ALPHA30_SEED
    raise ValueError(f"unknown alpha30 list {variant!r}; choose from {list(ALPHA30_VARIANTS)}")


def alpha30_instruments(
    variant: str = DEFAULT_ALPHA30_VARIANT, exchange: str = "NSE"
) -> list[InstrumentId]:
    """``alpha30_symbols(variant)`` as ``InstrumentId`` objects, in list order."""
    return [InstrumentId(sym, exchange) for sym in alpha30_symbols(variant)]


def banknifty_members(
    exchange: str = "NSE",
    *,
    as_of: dt.date | None = None,
    data_dir: Path | None = None,
) -> list[InstrumentId]:
    """Bank Nifty members from the core registry or a catalog history, else raise.

    Raises:
        UniverseUnavailable: when neither source knows ``banknifty``.
    """
    try:
        return resolve_universe("banknifty", exchange=exchange)
    except ValueError:
        pass
    day = as_of or dt.datetime.now(tz=dt.UTC).date()
    try:
        members = resolve_universe("banknifty", exchange=exchange, as_of=day, data_dir=data_dir)
    except ValueError:
        members = []
    if members:
        return members
    raise UniverseUnavailable(
        "no Bank Nifty constituents available: the Honba core registers only nifty50 and "
        "nifty200_alpha30, and there is no <data root>/constituents/banknifty.parquet. "
        "Save a history with honba.markets.india.universes.save_universe_history(...) "
        "from a source you trust; this example will not invent a member list."
    )
