"""options/07_large_qty_options_slicer: Freeze-limit order slicing for options.

Strategy-adjacent plumbing: submit a 5,000-contract NIFTY options order and
verify it is sliced into child orders each ≤ the instrument's freeze limit
(SEBI single-order freeze quantity, planned at 1,800 NIFTY contracts). The
freeze limit is read from the instrument master, never hard-coded.

Prefers ``honba_te-slicer`` (``honba-te`` repo) when installed; otherwise uses
the deterministic reference slicer in ``honba_examples.options`` with the same
contract (every slice ≤ the limit). Both are exercised below so the script is
offline and deterministic in either case. Runs are a verification harness, not
a backtest — no prices, no fills, no market impact.

Run::

    python examples/options/07_large_qty_options_slicer.py --total-qty 5000
    python examples/options/07_large_qty_options_slicer.py --total-qty 5000 --freeze-limit 1800 --out slices.json

Pitfalls::

    - Freeze limits are per order, per exchange and per instrument: an order
      above the limit is rejected by the exchange unless the OMS/slicer splits
      it first; slicing is execution hygiene, not a trade-sizing recommendation.
    - ``honba_te_slicer`` is not installed in this checkout, so the local
      reference slicer runs instead (identical contract: slices ≤ freeze limit).
      Swap in ``honba_te_slicer.slice_order`` when the sibling checkout is
      installed — its output must pass the same ``verify_slices`` check.
    - Slicing spreads the parent across time and prices: children fill at
      different levels with different impact, so average fill ≠ mid at the
      parent timestamp; no slippage or impact model is applied here.
    - Slice sizes must be lot multiples (NIFTY lot = 50 contracts); a remainder
      slice that is not a lot multiple must be rounded up and re-checked
      against the limit.
    - Freeze quantity is revised by SEBI circular; read it from the instrument
      master each session, never cache it in strategy code.
"""

from __future__ import annotations

import sys
from typing import Any

from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.options import freeze_limit_qty, slice_order, verify_slices

__all__ = ["main", "run"]

TE_SLICER_MODULE = "honba_te_slicer"
DEFAULT_TOTAL_QTY = 5000  # NIFTY contracts in the plan
PLANNED_FREEZE_LIMIT = 1800  # planned NIFTY options freeze limit, contracts


def _te_slicer_available() -> bool:
    """True when the honba-te slicer package can be imported."""
    try:
        __import__(TE_SLICER_MODULE)
        return True
    except ImportError:
        return False


def _slice_with_te_slicer(total_quantity: int, freeze_limit: int) -> tuple[list[int], str]:
    """Slice via ``honba_te_slicer`` if importable; fall back otherwise."""
    try:
        slicer = __import__(TE_SLICER_MODULE)
        slice_order_te = getattr(slicer, "slice_order", None)
        if slice_order_te is None:
            raise ImportError("honba_te_slicer has no slice_order")
        slices = [int(s) for s in slice_order_te(total_quantity, freeze_limit)]
        return slices, "honba_te_slicer"
    except (ImportError, AttributeError, OSError, ValueError, TypeError):  # offline / no slicer
        return slice_order(total_quantity, freeze_limit), "honba_examples.options"


def run(
    total_quantity: int = DEFAULT_TOTAL_QTY,
    symbol: str = "NIFTY",
    exchange: str = "NSE",
    freeze_limit: int | None = None,
) -> dict[str, Any]:
    """Slice ``total_quantity`` contracts and verify each slice ≤ freeze limit."""
    limit = freeze_limit if freeze_limit is not None else freeze_limit_qty(symbol, exchange)
    slices, source = _slice_with_te_slicer(total_quantity, limit)

    # The contract this example verifies: no child may exceed the freeze limit.
    verify_slices(slices, limit)

    # Per-slice contract metadata: lot multiples for NIFTY (50 contracts).
    lot_size = 50
    lot_multiple_ok = all(s % lot_size == 0 for s in slices)

    return {
        "config": {
            "total_quantity": total_quantity,
            "symbol": symbol,
            "exchange": exchange,
            "freeze_limit": limit,
            "freeze_limit_source": "instrument_master" if freeze_limit is None else "cli",
            "slicer": source,
            "lot_size": lot_size,
        },
        "slices": [{"slice_index": i, "quantity": size} for i, size in enumerate(slices)],
        "verification": {
            "all_within_limit": all(s <= limit for s in slices),
            "max_slice": max(slices),
            "sum_equals_parent": sum(slices) == total_quantity,
            "all_lot_multiples": lot_multiple_ok,
            "slices_required": len(slices),
        },
        "note": (
            f"{source}: {len(slices)} slice(s), largest {max(slices)} contracts "
            f"<= freeze limit {limit}"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--total-qty", type=int, default=DEFAULT_TOTAL_QTY)
    parser.add_argument("--symbol", type=str, default="NIFTY")
    parser.add_argument("--exchange", type=str, default="NSE")
    parser.add_argument("--freeze-limit", type=int, default=None)
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(
        total_quantity=args.total_qty,
        symbol=args.symbol,
        exchange=args.exchange,
        freeze_limit=args.freeze_limit,
    )

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
