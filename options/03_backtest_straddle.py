"""options/03_backtest_straddle: Backtest a long ATM straddle.

Strategy: buy the ATM call and put at entry, hold to expiry, and compute P&L across
underlying moves. Teaches a long-volatility payoff: max loss is the premium paid,
breakeven at strike ± total premium, unlimited upside. Legs are hand-rolled here —
no `Strategy` subclass; runs are offline and deterministic.

Run::

    python options/03_backtest_straddle.py --underlying NIFTY --expiry 2024-12-26
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba.analytics.greeks import bs_price
from honba.domain.option import OptionKind

from honba_examples.cli import add_out_arg, emit_json, make_parser

__all__ = ["main", "run"]


def run(
    underlying: str = "NIFTY",
    expiry: dt.date | None = None,
    spot_at_entry: float = 24000.0,
    strike: float | None = None,
    days_to_expiry: int = 7,
    iv: float = 0.15,
    lot_size: int = 50,
    premium_call: float | None = None,
    premium_put: float | None = None,
) -> dict[str, Any]:
    expiry = expiry or dt.date(2024, 12, 26)
    strike = strike or round(spot_at_entry / 50) * 50
    tau = days_to_expiry / 365.0
    r = 0.07

    # Premium via Black-Scholes or provided
    if premium_call is None:
        premium_call = bs_price(OptionKind.CALL, spot_at_entry, strike, tau, iv, r)
    if premium_put is None:
        premium_put = bs_price(OptionKind.PUT, spot_at_entry, strike, tau, iv, r)

    total_premium = premium_call + premium_put
    lower_breakeven = strike - total_premium
    upper_breakeven = strike + total_premium

    # P&L at expiry for various underlying prices
    pnl_zones = []
    for spot_at_expiry in [
        spot_at_entry - 500,
        spot_at_entry - 300,
        spot_at_entry - 100,
        strike,
        spot_at_entry + 100,
        spot_at_entry + 300,
        spot_at_entry + 500,
    ]:
        call_payoff = max(0.0, spot_at_expiry - strike)
        put_payoff = max(0.0, strike - spot_at_expiry)
        pnl = (call_payoff + put_payoff - total_premium) * lot_size
        pnl_zones.append(
            {
                "spot_at_expiry": spot_at_expiry,
                "call_payoff": call_payoff,
                "put_payoff": put_payoff,
                "total_payoff": call_payoff + put_payoff,
                "pnl": pnl,
            }
        )

    return {
        "config": {
            "underlying": underlying,
            "expiry": expiry.isoformat(),
            "strike": strike,
            "spot_at_entry": spot_at_entry,
            "days_to_expiry": days_to_expiry,
            "iv": iv,
            "lot_size": lot_size,
        },
        "straddle": {
            "premium_call": premium_call,
            "premium_put": premium_put,
            "total_premium": total_premium,
            "lower_breakeven": lower_breakeven,
            "upper_breakeven": upper_breakeven,
            "max_loss": -total_premium * lot_size,
            "max_profit": "unlimited",
        },
        "pnl_zones": pnl_zones,
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--underlying", type=str, default="NIFTY")
    parser.add_argument("--expiry", type=lambda s: dt.date.fromisoformat(s), default=None)
    parser.add_argument("--spot", type=float, default=24000.0)
    parser.add_argument("--strike", type=float, default=None)
    parser.add_argument("--days-to-expiry", type=int, default=7)
    parser.add_argument("--iv", type=float, default=0.15)
    parser.add_argument("--lot-size", type=int, default=50)
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(
        underlying=args.underlying,
        expiry=args.expiry,
        spot_at_entry=args.spot,
        strike=args.strike,
        days_to_expiry=args.days_to_expiry,
        iv=args.iv,
        lot_size=args.lot_size,
    )

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
