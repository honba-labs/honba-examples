"""options/05_expiry_day_strategy: Intraday expiry-day gamma/theta strategy.

Strategy: on expiry day gamma spikes near ATM while theta accelerates, so this
example simulates a delta-neutral gamma-scalping position — straddle at the open,
rebalance delta intraday, and capture theta decay. Legs are hand-rolled here — no
`Strategy` subclass; the intraday path is a seeded GBM, so runs are offline and
deterministic.

Run::

    python options/05_expiry_day_strategy.py --underlying BANKNIFTY --expiry 2024-12-25
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba.analytics.greeks import bs_greeks, bs_price
from honba.domain.option import OptionKind

__all__ = ["main", "run"]


def _simulate_intraday_path(spot_open: float, drift: float, vol: float, steps: int, seed: int = 42) -> list[float]:
    """Generate intraday spot path with GBM."""
    import random
    random.seed(seed)
    dt_step = 1.0 / (steps * 365)  # daily fraction per step
    path = [spot_open]
    for _ in range(steps - 1):
        z = random.gauss(0, 1)
        spot = path[-1] * (1 + drift * dt_step + vol * (dt_step ** 0.5) * z)
        path.append(spot)
    return path


def run(
    underlying: str = "BANKNIFTY",
    expiry: dt.date | None = None,
    spot_at_open: float = 50000.0,
    strike: float | None = None,
    iv: float = 0.15,
    lot_size: int = 15,
    intraday_steps: int = 100,
    rebalance_threshold: float = 0.1,  # rebalance when delta exceeds this
) -> dict[str, Any]:
    expiry = expiry or dt.date(2024, 12, 25)
    strike = strike or round(spot_at_open / 100) * 100
    tau_open = 1.0 / 365.0  # expiry day: ~1 day to expiry
    r = 0.07

    # Initial straddle at open
    call_price_open = bs_price(OptionKind.CALL, spot_at_open, strike, tau_open, iv, r)
    put_price_open = bs_price(OptionKind.PUT, spot_at_open, strike, tau_open, iv, r)

    call_greeks_open = bs_greeks(OptionKind.CALL, spot_at_open, strike, tau_open, iv, r)
    put_greeks_open = bs_greeks(OptionKind.PUT, spot_at_open, strike, tau_open, iv, r)

    # Delta-neutral: long 1 call + long 1 put (net delta ≈ 0 near ATM)
    # Rebalance by adjusting position to stay delta-neutral
    path = _simulate_intraday_path(spot_at_open, 0.0, iv, intraday_steps)

    rebalance_log = []
    position = {"call": 1.0, "put": 1.0}  # quantities
    cash_flow = 0.0  # cash from rebalancing

    for i, spot in enumerate(path):
        tau = max(1e-6, (intraday_steps - i) / intraday_steps * tau_open)

        call_greeks = bs_greeks(OptionKind.CALL, spot, strike, tau, iv, r)
        put_greeks = bs_greeks(OptionKind.PUT, spot, strike, tau, iv, r)

        net_delta = position["call"] * call_greeks.delta + position["put"] * put_greeks.delta

        # Rebalance if |delta| > threshold
        if abs(net_delta) > rebalance_threshold:
            # Adjust position to neutralize delta (simplified: flip one leg)
            adjustment = -net_delta / call_greeks.delta
            position["call"] += adjustment
            # Cash cost of adjustment
            call_price = bs_price(OptionKind.CALL, spot, strike, tau, iv, r)
            cash_flow -= adjustment * call_price * lot_size
            rebalance_log.append({
                "step": i,
                "time_fraction": i / intraday_steps,
                "spot": spot,
                "net_delta_before": net_delta,
                "adjustment": adjustment,
                "cash_flow": -adjustment * call_price * lot_size,
            })

    # Final P&L at expiry (assume spot ends at last path value)
    spot_expiry = path[-1]
    call_payoff = max(0.0, spot_expiry - strike)
    put_payoff = max(0.0, strike - spot_expiry)

    total_payoff = (position["call"] * call_payoff + position["put"] * put_payoff) * lot_size
    initial_cost = (call_price_open + put_price_open) * lot_size
    final_pnl = total_payoff - initial_cost + cash_flow

    # Theta decay captured
    total_theta = 0.0
    for i, spot in enumerate(path):
        tau = max(1e-6, (intraday_steps - i) / intraday_steps * tau_open)
        call_greeks = bs_greeks(OptionKind.CALL, spot, strike, tau, iv, r)
        put_greeks = bs_greeks(OptionKind.PUT, spot, strike, tau, iv, r)
        total_theta += (position["call"] * call_greeks.theta + position["put"] * put_greeks.theta) * lot_size

    return {
        "config": {
            "underlying": underlying,
            "expiry": expiry.isoformat(),
            "strike": strike,
            "spot_at_open": spot_at_open,
            "iv": iv,
            "lot_size": lot_size,
            "intraday_steps": intraday_steps,
            "rebalance_threshold": rebalance_threshold,
        },
        "initial": {
            "call_price": call_price_open,
            "put_price": put_price_open,
            "total_premium": call_price_open + put_price_open,
            "call_delta": call_greeks_open.delta,
            "put_delta": put_greeks_open.delta,
        },
        "rebalances": rebalance_log,
        "summary": {
            "rebalance_count": len(rebalance_log),
            "total_rebalance_cash": cash_flow,
            "spot_at_expiry": spot_expiry,
            "final_pnl": final_pnl,
            "theta_captured": total_theta,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--underlying", type=str, default="BANKNIFTY")
    parser.add_argument("--expiry", type=lambda s: dt.date.fromisoformat(s), default=None)
    parser.add_argument("--spot", type=float, default=50000.0)
    parser.add_argument("--strike", type=float, default=None)
    parser.add_argument("--iv", type=float, default=0.15)
    parser.add_argument("--lot-size", type=int, default=15)
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--threshold", type=float, default=0.1)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(
        underlying=args.underlying,
        expiry=args.expiry,
        spot_at_open=args.spot,
        strike=args.strike,
        iv=args.iv,
        lot_size=args.lot_size,
        intraday_steps=args.steps,
        rebalance_threshold=args.threshold,
    )

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())