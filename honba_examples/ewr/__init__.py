"""Equal-weight rebalancing simulation engine.

Provides simulation and order sizing utilities to maintain equal weights across a basket
against a single shared cash pool, with support for cash-settlement cycles (e.g. T+1, T+2).

Submodules: :mod:`model` (constants, ``SimResult``), :mod:`sizing` (order sizing),
:mod:`schedule` (durations, rebalance sessions), :mod:`simulation` (the simulate loop) and
:mod:`reporting` (trade diffing, summary printing). Public names are re-exported here.
"""

from __future__ import annotations

from honba_examples.ewr.model import (
    DEFAULT_INITIAL_MARGIN,
    DEFAULT_LEVERAGE,
    DEFAULT_MAINTENANCE_MARGIN,
    FEE_RATE,
    REBALANCE_DAYS,
    STARTING_CAPITAL,
    TOLERANCE_PCT,
    SimResult,
)
from honba_examples.ewr.reporting import (
    format_changes,
    print_summary,
    rebalance_changes,
    trade_changes,
)
from honba_examples.ewr.schedule import parse_duration_to_days, rebalance_sessions
from honba_examples.ewr.simulation import simulate
from honba_examples.ewr.sizing import (
    _buy_shortfall,  # noqa: F401
    _compute_desired,  # noqa: F401
    _sell_excess,  # noqa: F401
    _sweep_residual_cash,  # noqa: F401
    _trade,  # noqa: F401
    buy_shortfall,
    rebalance_orders,
    sell_excess,
)

__all__ = [
    "DEFAULT_INITIAL_MARGIN",
    "DEFAULT_LEVERAGE",
    "DEFAULT_MAINTENANCE_MARGIN",
    "FEE_RATE",
    "REBALANCE_DAYS",
    "STARTING_CAPITAL",
    "TOLERANCE_PCT",
    "SimResult",
    "buy_shortfall",
    "format_changes",
    "parse_duration_to_days",
    "print_summary",
    "rebalance_changes",
    "rebalance_orders",
    "rebalance_sessions",
    "sell_excess",
    "simulate",
    "trade_changes",
]
