"""Options microstructure helpers used by the Phase 3 examples.

Core cost model: ``honba.markets.india.costs`` covers equity cash (delivery /
intraday); its F&O branch is intentionally a stub (returns 0) pending the NSE
F&O circular, so this module collects the option-specific pieces used by
``examples/options/06_*.py``, ``07_*.py`` and ``08_*.py``:

* option-premium STT on the *sell* side (statutory rate from the cost model /
  SEBI-NSE circular in force),
* ICCL-style settlement assumptions for index options (European, cash settled,
  premium/MTM T+1),
* SEBI single-order freeze limit read from the instrument master, and
* deterministic order slicing plus a verifier that every slice is ≤ the limit.

All functions are pure — no I/O, no wall clock — so the examples stay offline
and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# ---------------------------------------------------------------------------
# Statutory rates (NSE F&O / SEBI circulars in force for FY 2025-26)
# ---------------------------------------------------------------------------
#
# STT on the *sale* of index options: 0.0625 % of the option premium, payable by
# the seller. Revised from 0.017 % by the Finance Bill 2024 (effective
# 1 October 2024) under the relevant SEBI / NSE circulars. Rates change by
# circular; re-verify this constant against the latest SEBI/NSE circular before
# any paper or live run. Buyers pay no premium-side STT.
OPTION_SELL_STT_RATE = Decimal("0.000625")  # 0.0625 % of premium, sell side only

# ICCL (Indian Clearing Corporation Ltd) clears NSE F&O. Index options are
# European and cash-settled: premium and mark-to-market amounts settle T+1
# through ICCL; there is no physical delivery and no exercise STT on index
# options (unlike equity options, where exercise STT applies to intrinsic value).
ICCL_CLEARING_CORPORATION = "ICCL"
SETTLEMENT_CYCLE_DAYS = 1  # T+1


def option_sell_stt(premium_per_lot: float | Decimal, lots: int = 1) -> Decimal:
    """STT payable by the *seller* of one option leg.

    Charged on premium × qty once per sell fill; buyers pay no premium-side STT.
    Rounded to the paise once, like a contract note line.
    """
    return (Decimal(str(premium_per_lot)) * OPTION_SELL_STT_RATE * Decimal(lots)).quantize(
        Decimal("0.01")
    )


def fno_turnover(price: float | Decimal, quantity: float | Decimal) -> Decimal:
    """Notional turnover of one F&O fill (basis for exchange / IPFT / SEBI slabs)."""
    return Decimal(str(price)) * Decimal(str(quantity))


# ---------------------------------------------------------------------------
# Freeze limits (SEBI single-order freeze quantity)
# ---------------------------------------------------------------------------
#
# NIFTY options freeze limit is planned at 1,800 contracts. It is set by SEBI and
# revised by circular, so it must be read from the instrument master (lot size /
# tick size / freeze quantity travel together in the contract spec), never
# hard-coded in a strategy.  These helpers read it when an instrument master is
# reachable and fall back to the planned default so the example stays offline.


def _freeze_limit_from_master(symbol: str, exchange: str) -> int | None:
    """Best-effort freeze limit from the honba instrument master.

    Returns ``None`` when nothing is installed/reachable offline.
    """
    try:  # honba client instrument registry, when available
        from honba.client import get_instrument  # type: ignore[attr-defined]

        inst = get_instrument(symbol, exchange)
        if inst is not None:
            limit = getattr(inst, "freeze_limit_qty", None) or getattr(inst, "freeze_limit", None)
            if limit is not None:
                return int(limit)
    except (ImportError, AttributeError, OSError, ValueError, TypeError):  # offline store
        return None
    return None


def freeze_limit_qty(symbol: str = "NIFTY", exchange: str = "NSE") -> int:
    """Freeze-limit quantity for an instrument.

    Reads the instrument master first; falls back to the planned 1,800 NIFTY
    contracts when no master is reachable (offline / synthetic runs).
    """
    master = _freeze_limit_from_master(symbol, exchange)
    if master is not None and master > 0:
        return master
    return 1800


def slice_order(total_quantity: int, freeze_limit_qty: int) -> list[int]:
    """Split ``total_quantity`` into child slices each ≤ the freeze limit.

    Deterministic: whole slices of exactly the limit, then a final remainder slice.
    Every slice is a positive lot multiple when both inputs are.
    """
    if total_quantity <= 0:
        raise ValueError("total_quantity must be positive")
    if freeze_limit_qty <= 0:
        raise ValueError("freeze_limit_qty must be positive")
    slices: list[int] = []
    remaining = total_quantity
    while remaining > 0:
        size = min(freeze_limit_qty, remaining)
        slices.append(size)
        remaining -= size
    return slices


def verify_slices(slices: list[int], freeze_limit_qty: int) -> None:
    """Raise ``ValueError`` when any slice exceeds the freeze limit."""
    for index, size in enumerate(slices):
        if size > freeze_limit_qty:
            raise ValueError(f"slice {index} ({size}) exceeds freeze limit {freeze_limit_qty}")


# ---------------------------------------------------------------------------
# Greek risk bounds
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GreekRiskBounds:
    """Per-step risk limits for an options position (Greek space)."""

    max_net_delta: float = 0.25  # |net delta| beyond this triggers a delta hedge
    max_gamma: float = 0.005  # |net gamma| beyond this is logged as a breach
    max_vega: float = 0.50  # |net vega| beyond this is logged as a breach


def check_greek_bounds(
    net_delta: float,
    net_gamma: float,
    net_vega: float,
    bounds: GreekRiskBounds | None = None,
) -> list[str]:
    """Return human-readable breach messages for the given Greeks (empty = OK)."""
    bounds = bounds or GreekRiskBounds()
    breaches: list[str] = []
    if abs(net_delta) > bounds.max_net_delta:
        breaches.append(f"net_delta {net_delta:+.4f} beyond bound {bounds.max_net_delta:+.4f}")
    if abs(net_gamma) > bounds.max_gamma:
        breaches.append(f"net_gamma {net_gamma:+.4f} beyond bound {bounds.max_gamma:+.4f}")
    if abs(net_vega) > bounds.max_vega:
        breaches.append(f"net_vega {net_vega:+.4f} beyond bound {bounds.max_vega:+.4f}")
    return breaches


__all__ = [
    "ICCL_CLEARING_CORPORATION",
    "OPTION_SELL_STT_RATE",
    "SETTLEMENT_CYCLE_DAYS",
    "GreekRiskBounds",
    "check_greek_bounds",
    "fno_turnover",
    "freeze_limit_qty",
    "option_sell_stt",
    "slice_order",
    "verify_slices",
]
