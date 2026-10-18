"""options/05_expiry_day_strategy: Intraday expiry-day gamma/theta strategy.

Strategy: on expiry day gamma spikes near ATM while theta accelerates, so this
example simulates a delta-neutral gamma-scalping position — a long straddle at the open,
rebalanced intraday; it is long gamma and so pays theta (``theta_pnl`` is negative).
Legs are hand-rolled here — no `Strategy` subclass; the intraday path is a seeded GBM,
so runs are offline and deterministic.

Run::

    python options/05_expiry_day_strategy.py --underlying BANKNIFTY --expiry 2024-12-25

Pitfalls::

    - Simulated, so it proves nothing about edge: one seeded GBM path (seed 42, re-seeded on each
      call, which also resets the global random state) whose volatility equals the iv used for
      pricing. Realised equals implied, so the result is noise around the theta-versus-gamma
      breakeven, and a single path says nothing about edge. Real expiry-day profit depends on
      realised versus implied vol, which needs real intraday option data.
    - Time: tau_open is 1/365 (one calendar day) and the session is cut into --steps equal slices.
      The real session is about 09:15 to 15:30 IST, and expiry-day implied vol is not flat through
      it. The last path point is treated as expiry, so the payoff is taken at a spot whose tau is
      still positive.
    - Hedging by call quantity: delta is neutralised by resizing the call leg, dividing by the
      call's delta. That is unbounded when the call is far OTM late in the day, and pin risk at the
      strike is exactly where gamma and this hedge are least stable. Fills are at Black-Scholes
      values; bid/ask, slippage, STT on exercised legs and freeze quantities on large orders are
      ignored.
    - theta_pnl is signed theta accrued by the position held at each step: a long straddle pays it.
      The expiry is only a label (it is not used in pricing), and lot_size is an argument, so
      confirm the real expiry calendar, holidays and the current lot size before trusting any scale.
"""

from __future__ import annotations

import datetime as dt
import sys
from typing import Any

from honba.analytics.greeks import bs_greeks, bs_price
from honba.domain.option import OptionKind

from honba_examples.cli import add_out_arg, emit_json, make_parser

__all__ = ["main", "run"]


def _simulate_intraday_path(
    spot_open: float, drift: float, vol: float, steps: int, seed: int = 42
) -> list[float]:
    """Generate intraday spot path with GBM."""
    import random

    random.seed(seed)
    dt_step = 1.0 / (steps * 365)  # daily fraction per step
    path = [spot_open]
    for _ in range(steps - 1):
        z = random.gauss(0, 1)
        spot = path[-1] * (1 + drift * dt_step + vol * (dt_step**0.5) * z)
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
    theta_pnl = 0.0  # time decay accrued by the position held at each step (negative when long)

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
            rebalance_log.append(
                {
                    "step": i,
                    "time_fraction": i / intraday_steps,
                    "spot": spot,
                    "net_delta_before": net_delta,
                    "adjustment": adjustment,
                    "cash_flow": -adjustment * call_price * lot_size,
                }
            )

        # theta is quoted per day and each step spans 1/steps of the one-day session
        theta_pnl += (
            (position["call"] * call_greeks.theta + position["put"] * put_greeks.theta)
            * lot_size
            / intraday_steps
        )

    # Final P&L at expiry (assume spot ends at last path value)
    spot_expiry = path[-1]
    call_payoff = max(0.0, spot_expiry - strike)
    put_payoff = max(0.0, strike - spot_expiry)

    total_payoff = (position["call"] * call_payoff + position["put"] * put_payoff) * lot_size
    initial_cost = (call_price_open + put_price_open) * lot_size
    final_pnl = total_payoff - initial_cost + cash_flow

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
            "theta_pnl": theta_pnl,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--underlying", type=str, default="BANKNIFTY")
    parser.add_argument("--expiry", type=lambda s: dt.date.fromisoformat(s), default=None)
    parser.add_argument("--spot", type=float, default=50000.0)
    parser.add_argument("--strike", type=float, default=None)
    parser.add_argument("--iv", type=float, default=0.15)
    parser.add_argument("--lot-size", type=int, default=15)
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--threshold", type=float, default=0.1)
    add_out_arg(parser)
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

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
