"""stocksharp/04_market_maker: Avellaneda-Stoikov quoting with OBI filter.

Strategy: passive bid/ask market making around a simulated mid price.  Each
step the reservation price and half-spread come from the Avellaneda-Stoikov
model:

    reservation = mid - q * gamma * sigma^2 * (T - t)
    half_spread = 0.5 * (gamma * sigma^2 * (T - t) + (2/gamma) * ln(1 + gamma/k))

so quotes skew with inventory ``q`` (long inventory lowers both quotes to
encourage selling) and widen as risk/horizon grows.  An order-book-imbalance
filter (OBI = (bid_size - ask_size) / (bid_size + ask_size)) pauses quoting
when |OBI| > limit — do not quote into a one-sided book.

Fills are a simple distance-to-mid probability model; every fill updates
inventory and cash, and P&L is marked to market.  Runs offline and deterministic
on a seeded mid path; no ``Strategy`` subclass.

Run::

    python examples/stocksharp/04_market_maker.py --steps 240 --seed 42 --gamma 0.1 --k 1.5
    python examples/stocksharp/04_market_maker.py --inventory-limit 200 --obi-limit 0.6 --out mm.json

Pitfalls::

    - Avellaneda-Stoikov parameters (gamma risk aversion, k order-flow
      intensity, sigma vol) are per-asset calibrations; the defaults here are
      illustrative, not tradable.
    - The fill model fills a quote with a fixed probability when the mid moves
      through it; real queue position, priority, latency and adverse selection
      (informed flow toxicity) are not modelled.
    - The OBI filter pauses quoting but does not model the flow missed while
      paused, nor does it skew size by imbalance — only the pause gate.
    - Inventory is never rebalanced and overnight inventory risk is unhedged;
      no borrow, rebates, exchange fees or transaction costs are applied.
    - Reservation price drifts with inventory, so a persistent order-flow
      imbalance can drift quotes away from mid and earn adverse-selection loss.
"""

from __future__ import annotations

import math
import random
import sys
from typing import Any

from honba_examples.cli import add_out_arg, emit_json, make_parser

__all__ = ["main", "run"]

DEFAULT_STEPS = 240  # one quote decision per minute, 4-hour session


def _simulate_mid_path(
    steps: int, seed: int = 42, start: float = 24000.0, vol: float = 0.01
) -> list[float]:
    """Seeded GBM mid-price path; re-seeded per call."""
    rng = random.Random(seed)
    path = [start]
    for _ in range(steps - 1):
        z = rng.gauss(0.0, 1.0)
        path.append(path[-1] * (1.0 + vol * z))
    return path


def _simulate_book(mid: float, seed: int = 0) -> tuple[float, float]:
    """Synthetic bid/ask sizes at the current book (returns bid_size, ask_size)."""
    rng = random.Random(seed)
    base = 100.0
    bid = max(1.0, base + rng.gauss(0.0, 40.0))
    ask = max(1.0, base + rng.gauss(0.0, 40.0))
    return bid, ask


def run(
    steps: int = DEFAULT_STEPS,
    seed: int = 42,
    gamma: float = 0.1,
    k: float = 1.5,
    sigma: float = 0.02,
    inventory_limit: int = 200,
    obi_limit: float = 0.6,
    start_mid: float = 24000.0,
    vol: float = 0.01,
    quote_size: float = 50.0,
) -> dict[str, Any]:
    """Market-make for ``steps`` decisions; return quotes, fills and P&L."""
    mid_path = _simulate_mid_path(steps, seed=seed, start=start_mid, vol=vol)

    inventory = 0  # signed units
    cash = 0.0
    fills: list[dict[str, Any]] = []
    quotes: list[dict[str, Any]] = []
    paused_steps = 0

    T_total = steps
    for t in range(steps):
        mid = mid_path[t]
        time_to_go = (T_total - t) / T_total  # fraction of session remaining

        # Avellaneda-Stoikov reservation price and spread.
        reservation = mid - inventory * gamma * sigma * sigma * time_to_go
        half_spread = 0.5 * (
            gamma * sigma * sigma * time_to_go + (2.0 / gamma) * math.log1p(gamma / k)
        )
        bid = reservation - half_spread
        ask = reservation + half_spread

        # Order-book-imbalance filter: pause quoting in a one-sided book.
        bid_size, ask_size = _simulate_book(mid, seed=seed + t * 7 + 3)
        obi = (bid_size - ask_size) / (bid_size + ask_size)
        paused = abs(obi) > obi_limit
        if paused:
            paused_steps += 1
            quotes.append(
                {
                    "step": t,
                    "mid": mid,
                    "paused": True,
                    "obi": obi,
                }
            )
            continue

        quotes.append(
            {
                "step": t,
                "mid": mid,
                "paused": False,
                "obi": obi,
                "reservation": reservation,
                "bid": bid,
                "ask": ask,
                "spread": ask - bid,
                "inventory": inventory,
            }
        )

        # Fill model (next mid crosses this step's quotes): if the market falls
        # through our bid we are lifted on the bid (we buy); if it rallies
        # through our ask we sell.  Fill is assumed certain once crossed — an
        # optimistic, queue-priority-free model documented in the pitfalls.
        if t + 1 < len(mid_path):
            next_mid = mid_path[t + 1]
            if inventory < inventory_limit and next_mid <= bid:
                inventory += quote_size
                cash -= quote_size * bid
                fills.append(
                    {"step": t + 1, "side": "buy", "price": bid, "qty": quote_size, "mid": next_mid}
                )
            elif inventory > -inventory_limit and next_mid >= ask:
                inventory -= quote_size
                cash += quote_size * ask
                fills.append(
                    {
                        "step": t + 1,
                        "side": "sell",
                        "price": ask,
                        "qty": quote_size,
                        "mid": next_mid,
                    }
                )

    final_mid = mid_path[-1]
    mark_to_market = cash + inventory * final_mid

    return {
        "config": {
            "steps": steps,
            "seed": seed,
            "gamma": gamma,
            "k": k,
            "sigma": sigma,
            "inventory_limit": inventory_limit,
            "obi_limit": obi_limit,
            "quote_size": quote_size,
        },
        "quotes": quotes,
        "fills": fills,
        "summary": {
            "quote_steps": len(quotes),
            "paused_steps": paused_steps,
            "fill_count": len(fills),
            "final_inventory": inventory,
            "final_mid": final_mid,
            "cash": cash,
            "mark_to_market_pnl": mark_to_market,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--steps", type=int, default=DEFAULT_STEPS)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--gamma", type=float, default=0.1)
    parser.add_argument("--k", type=float, default=1.5)
    parser.add_argument("--sigma", type=float, default=0.02)
    parser.add_argument("--inventory-limit", type=int, default=200)
    parser.add_argument("--obi-limit", type=float, default=0.6)
    parser.add_argument("--start-mid", type=float, default=24000.0)
    parser.add_argument("--vol", type=float, default=0.01)
    parser.add_argument("--quote-size", type=float, default=50.0)
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(
        steps=args.steps,
        seed=args.seed,
        gamma=args.gamma,
        k=args.k,
        sigma=args.sigma,
        inventory_limit=args.inventory_limit,
        obi_limit=args.obi_limit,
        start_mid=args.start_mid,
        vol=args.vol,
        quote_size=args.quote_size,
    )

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
