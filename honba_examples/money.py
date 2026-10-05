"""Money at the ledger boundary, in integer paise (ADR 0011).

Prices and quantities stay ``float`` (market observations); anything settled,
summed or compared - cash, notional, fees, equity - is an ``int`` number of paise.
Conversion rounds half away from zero, on the decimal value the float was written
as, so ``1.005`` rupees is 101 paise rather than float's 100. Floats come back
out only for display and for statistics (returns, Sharpe, drawdown).
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

from honba.domain.order import OrderSide
from honba.markets.india.costs import nse_equity_delivery_breakdown

__all__ = [
    "PAISE_PER_RUPEE",
    "delivery_cost_paise",
    "notional_paise",
    "paise_to_rupees",
    "to_paise",
]

PAISE_PER_RUPEE = 100
_I64_MAX = 2**63 - 1


def _decimal(value: float) -> Decimal:
    if not math.isfinite(value):
        raise ValueError(f"money amount must be finite, got {value!r}")
    # repr() is the shortest decimal that round-trips, i.e. the value as written.
    return Decimal(repr(float(value)))


def _round_paise(paise: Decimal) -> int:
    # Decimal's ROUND_HALF_UP rounds half away from zero (symmetric for +/-).
    out = int(paise.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if abs(out) > _I64_MAX:
        raise ValueError("money amount exceeds i64 paise")
    return out


def to_paise(rupees: float) -> int:
    """Rupees -> integer paise, half away from zero. Rejects NaN/inf and i64 overflow."""
    return _round_paise(_decimal(rupees) * PAISE_PER_RUPEE)


def notional_paise(quantity: float, price: float) -> int:
    """``quantity * price`` in paise, multiplied exactly and rounded once (``mul_qty``)."""
    return _round_paise(_decimal(quantity) * _decimal(price) * PAISE_PER_RUPEE)


def paise_to_rupees(paise: int) -> float:
    """Paise -> rupees as a float. Lossy by definition: display and statistics only."""
    return paise / PAISE_PER_RUPEE


def delivery_cost_paise(side: OrderSide, quantity: float, price: float) -> int:
    """NSE equity delivery (CNC) cost of one fill, in paise.

    Uses the core cost schedule (``honba.markets.india.costs``) and rounds each
    leg once before summing, so the total is one a broker contract note can show.
    """
    legs = nse_equity_delivery_breakdown(side, quantity, price)
    return sum(
        to_paise(leg)
        for leg in (
            legs.brokerage,
            legs.stt,
            legs.exchange,
            legs.sebi,
            legs.ipft,
            legs.stamp_duty,
            legs.gst,
        )
    )
