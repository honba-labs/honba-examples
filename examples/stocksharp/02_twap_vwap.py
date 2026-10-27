"""stocksharp/02_twap_vwap: Execution slicers — TWAP and VWAP (port of StockSharp slicers).

Strategy-adjacent execution: work a 5,000-contract NIFTY parent order through the
session (09:15–15:30 IST) using either

* TWAP — equal quantity per time slice,
* VWAP — quantity proportional to each slice's forecast volume share (U-shaped
  intraday volume: heavy at the open and close, light midday),

with deterministic microsecond jitter on every child order time (seeded RNG), so
the same parent and seed always produce the same child stream.  Every child is
checked against the instrument's freeze limit via ``honba_examples.options``:
``verify_slices`` raises when a child exceeds the limit, so a parent above the
freeze limit can never be submitted whole.

Runs offline and deterministic; no prices or fills are modelled — it is an
execution-slice verification harness, not a backtest.

Run::

    python examples/stocksharp/02_twap_vwap.py --scheme twap --parent-qty 5000 --slices 75
    python examples/stocksharp/02_twap_vwap.py --scheme vwap --parent-qty 5000 --slices 75 --seed 7 --out slices.json

Pitfalls::

    - The VWAP volume curve is a forecast; using the realised curve is
      look-ahead in a live algorithm — use the historical average curve instead.
    - Freeze limits are per child order: a 5,000-contract parent exceeds the
      planned 1,800 NIFTY freeze limit, so it must be sliced; ``verify_slices``
      enforces this and raises on any oversized child.
    - Child times are jittered within their slice (microseconds), but child
      prices are not modelled — real jitter also randomises price and venue.
    - Slices are time- or volume-proportional, not participation-rate based:
      no slice size reacts to the order book or to realised volume.
    - Deterministic seed makes the stream repeatable; a production RNG draws
      from entropy and differs every run.
"""

from __future__ import annotations

import datetime as dt
import math
import random
import sys
from typing import Any

from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.options import freeze_limit_qty, verify_slices
from honba_examples.options import slice_order as _ref_slice_order

__all__ = ["main", "run"]

SESSION_OPEN = dt.time(9, 15)
SESSION_CLOSE = dt.time(15, 30)
SESSION_MINUTES = (SESSION_CLOSE.hour * 60 + SESSION_CLOSE.minute) - (
    SESSION_OPEN.hour * 60 + SESSION_OPEN.minute
)
DEFAULT_PARENT_QTY = 5000
DEFAULT_SLICES = 75  # 5-min slices across a 375-minute session


def _intraday_volume_weights(slices: int, seed: int = 0) -> list[float]:
    """U-shaped intraday volume weights (one per slice), sums to 1."""
    rng = random.Random(seed)
    weights = []
    for i in range(slices):
        t = (i + 0.5) / slices  # 0..1 through the session
        # U-shape: high near open and close, low midday
        w = 1.0 + 2.0 * math.exp(-(((t - 0.5) / 0.18) ** 2))
        w *= 1.0 + 0.15 * rng.gauss(0.0, 1.0)  # day-to-day noise
        weights.append(max(w, 0.05))
    total = sum(weights)
    return [w / total for w in weights]


def _jittered_times(
    opens: list[dt.datetime],
    closes: list[dt.datetime],
    seed: int,
    jitter_seconds: float = 30.0,
) -> list[dt.datetime]:
    """One jittered child time per slice, uniform within the slice window."""
    rng = random.Random(seed)
    out = []
    for op, cl in zip(opens, closes, strict=True):
        span = (cl - op).total_seconds()
        out.append(
            op + dt.timedelta(seconds=rng.uniform(0.0, max(span, 1.0) * jitter_seconds / 60.0))
        )
    return out


def run(
    scheme: str = "twap",
    parent_qty: int = DEFAULT_PARENT_QTY,
    slices: int = DEFAULT_SLICES,
    symbol: str = "NIFTY",
    exchange: str = "NSE",
    seed: int = 42,
    jitter_seconds: float = 30.0,
    freeze_limit: int | None = None,
) -> dict[str, Any]:
    """Slice ``parent_qty`` with TWAP or VWAP pacing; verify against freeze limit."""
    if scheme not in ("twap", "vwap"):
        raise ValueError(f"scheme must be 'twap' or 'vwap', got {scheme!r}")
    if parent_qty <= 0:
        raise ValueError("parent_qty must be positive")
    if slices <= 0:
        raise ValueError("slices must be positive")

    freeze_limit = freeze_limit if freeze_limit is not None else freeze_limit_qty(symbol, exchange)
    session_start = dt.datetime.combine(dt.date(2026, 4, 7), SESSION_OPEN)
    session_end = dt.datetime.combine(dt.date(2026, 4, 7), SESSION_CLOSE)
    step = (session_end - session_start) / slices
    opens = [session_start + i * step for i in range(slices)]
    closes = [session_start + (i + 1) * step for i in range(slices)]

    if scheme == "twap":
        raw_qtys = [parent_qty / slices] * slices
    else:
        weights = _intraday_volume_weights(slices, seed)
        raw_qtys = [parent_qty * w for w in weights]

    # Integer lots: distribute the remainder deterministically (largest remainder).
    qtys = [math.floor(q) for q in raw_qtys]
    remainder = parent_qty - sum(qtys)
    fractions = sorted(range(slices), key=lambda i: raw_qtys[i] - qtys[i], reverse=True)
    for i in fractions[:remainder]:
        qtys[i] += 1

    times = _jittered_times(opens, closes, seed, jitter_seconds)

    children = [
        {
            "slice_index": i,
            "scheduled_time": opens[i].time().isoformat(timespec="microseconds"),
            "jittered_time": times[i].time().isoformat(timespec="microseconds"),
            "quantity": qtys[i],
        }
        for i in range(slices)
    ]

    verify_slices(qtys, freeze_limit)
    ref_slices = _ref_slice_order(parent_qty, freeze_limit)

    return {
        "config": {
            "scheme": scheme,
            "parent_qty": parent_qty,
            "slices": slices,
            "symbol": symbol,
            "exchange": exchange,
            "freeze_limit": freeze_limit,
            "freeze_limit_source": "instrument_master",
            "seed": seed,
            "jitter_seconds": jitter_seconds,
        },
        "children": children,
        "verification": {
            "all_within_limit": all(q <= freeze_limit for q in qtys),
            "max_child_qty": max(qtys),
            "sum_equals_parent": sum(qtys) == parent_qty,
            "children_required": len(children),
            "reference_slice_order": ref_slices,
        },
        "note": (
            f"{scheme.upper()}: {len(children)} child(ren), largest "
            f"{max(qtys)} contracts <= freeze limit {freeze_limit}"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--scheme", type=str, choices=["twap", "vwap"], default="twap")
    parser.add_argument("--parent-qty", type=int, default=DEFAULT_PARENT_QTY)
    parser.add_argument("--slices", type=int, default=DEFAULT_SLICES)
    parser.add_argument("--symbol", type=str, default="NIFTY")
    parser.add_argument("--exchange", type=str, default="NSE")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--jitter-seconds", type=float, default=30.0)
    parser.add_argument("--freeze-limit", type=int, default=None)
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(
        scheme=args.scheme,
        parent_qty=args.parent_qty,
        slices=args.slices,
        symbol=args.symbol,
        exchange=args.exchange,
        seed=args.seed,
        jitter_seconds=args.jitter_seconds,
        freeze_limit=args.freeze_limit,
    )

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
