"""Simulation constants and the :class:`SimResult` container."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STARTING_CAPITAL = 1_000_000.0
FEE_RATE = 0.001
REBALANCE_DAYS = 15
TOLERANCE_PCT = 0.05
DEFAULT_LEVERAGE = 2.0
DEFAULT_INITIAL_MARGIN = 0.5
DEFAULT_MAINTENANCE_MARGIN = 0.25


@dataclass
class SimResult:
    """Outcome of one equal-weight rebalance simulation."""

    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    rebalances: list[dict[str, Any]] = field(default_factory=list)
    rebalance_changes: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    final_holdings: dict[str, int] = field(default_factory=dict)
    never_held: list[str] = field(default_factory=list)
    missing_data: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    margin_used_curve: list[float] = field(default_factory=list)
    max_margin_used_pct: float = 0.0
    margin_calls: int = 0
    leverage: float = 1.0
    initial_margin: float = DEFAULT_INITIAL_MARGIN
    maintenance_margin: float = DEFAULT_MAINTENANCE_MARGIN
    initial_corpus: float = STARTING_CAPITAL
    sip_amount: float = 0.0
    total_invested: float = STARTING_CAPITAL
    sips_executed: int = 0
