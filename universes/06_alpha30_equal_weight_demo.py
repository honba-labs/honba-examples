"""06_alpha30_equal_weight_demo: runnable equal-weight rebalance of Alpha 30.

The strategy class is ``Alpha30EqualWeightRebalance``: an Alpha-30 specialization of
``UniverseEqualWeightRebalance`` (from 04_alpha30_generic_rebalancer.py), rebalanced
every 15 trading days. Membership is resolved through the Honba universe API and
re-fetched on every rebalance so index joiners / leavers are applied:

    from honba.markets.india.universes import resolve_universe
    ids = resolve_universe("nifty200_alpha_30", exchange="NSE")

The ``__main__`` block resolves membership as a smoke test, so the example runs
offline and deterministically against the bundled universe map.

Run::

    python universes/06_alpha30_equal_weight_demo.py
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from honba.domain.instrument import InstrumentId
from honba.markets.india.universes import resolve_universe

_path_04 = Path(__file__).resolve().parent / "04_alpha30_generic_rebalancer.py"
_spec = importlib.util.spec_from_file_location("ex04_generic", _path_04)
assert _spec is not None and _spec.loader is not None
_mod_04 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod_04)
UniverseEqualWeightRebalance = _mod_04.UniverseEqualWeightRebalance


class Alpha30EqualWeightRebalance(UniverseEqualWeightRebalance):
    """Equal-weight Nifty200 Alpha 30 portfolio strategy rebalanced every 15 trading days."""

    name = "alpha30_equal_weight_rebalance"
    UNIVERSE = "nifty200_alpha_30"

    def __init__(
        self,
        capital: float = 1_000_000.0,
        allocation: float = 0.98,
        rebalance_days: int = 15,
        exchange: str = "NSE",
        universe: str = UNIVERSE,
    ) -> None:
        super().__init__(
            capital=capital,
            allocation=allocation,
            rebalance_days=rebalance_days,
            exchange=exchange,
            universe=universe,
        )


def main() -> None:
    # Smoke: resolve via engine API (falls back if Alpha 30 is still learning)
    try:
        members: list[InstrumentId] = resolve_universe("nifty200_alpha_30")
        print(f"nifty200_alpha_30 → {len(members)} names")
    except ValueError as e:
        print(f"universe not ready: {e}")
    print("Strategy uses resolve_universe() only — no hardcoded constituents.")


if __name__ == "__main__":
    main()
