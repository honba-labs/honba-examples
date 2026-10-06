"""options/04_backtest_iron_condor: Backtest iron condor (4-leg credit spread).

Sell OTM put spread + sell OTM call spread. Max profit = net credit.
Max loss = spread width - credit. Profit zone between short strikes.

Run::

    python options/04_backtest_iron_condor.py --underlying NIFTY --expiry 2024-12-26
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

from honba.domain.option import OptionKind, OptionStyle
from honba.domain.instrument import InstrumentId, InstrumentKind
from honba.analytics.greeks import bs_price
from honba_examples.jsonable import jsonable

__all__ = ["main", "run"]


def run(
    underlying: str = "NIFTY",
    expiry: dt.date | None = None,
    spot_at_entry: float = 24000.0,
    put_short_strike: float = 23500.0,
    put_long_strike: float = 23300.0,
    call_short_strike: float = 24500.0,
    call_long_strike: float = 24700.0,
    days_to_expiry: int = 7,
    iv: float = 0.15,
    lot_size: int = 50,
) -> dict[str, Any]:
    expiry = expiry or dt.date(2024, 12, 26)
    tau = days_to_expiry / 365.0
    r = 0.07

    # Premiums for each leg (all short positions)
    put_short = bs_price(OptionKind.PUT, spot_at_entry, put_short_strike, tau, iv, r)
    put_long = bs_price(OptionKind.PUT, spot_at_entry, put_long_strike, tau, iv, r)
    call_short = bs_price(OptionKind.CALL, spot_at_entry, call_short_strike, tau, iv, r)
    call_long = bs_price(OptionKind.CALL, spot_at_entry, call_long_strike, tau, iv, r)

    # Net credit = premium received - premium paid
    net_credit = (put_short - put_long) + (call_short - call_long)

    # Spread widths
    put_width = put_short_strike - put_long_strike
    call_width = call_long_strike - call_short_strike

    max_loss = max(put_width, call_width) * lot_size - net_credit * lot_size
    max_profit = net_credit * lot_size

    # Breakevens
    lower_breakeven = put_short_strike - net_credit
    upper_breakeven = call_short_strike + net_credit

    # P&L at expiry
    pnl_zones = []
    for spot_at_expiry in [spot_at_entry - 800, spot_at_entry - 500, spot_at_entry - 300,
                           put_short_strike, spot_at_entry, call_short_strike,
                           spot_at_entry + 300, spot_at_entry + 500, spot_at_entry + 800]:
        put_short_payoff = max(0.0, put_short_strike - spot_at_expiry)
        put_long_payoff = max(0.0, put_long_strike - spot_at_expiry)
        call_short_payoff = max(0.0, spot_at_expiry - call_short_strike)
        call_long_payoff = max(0.0, spot_at_expiry - call_long_strike)

        put_pnl = (put_short - put_long - put_short_payoff + put_long_payoff) * lot_size
        call_pnl = (call_short - call_long - call_short_payoff + call_long_payoff) * lot_size
        total_pnl = put_pnl + call_pnl

        pnl_zones.append({
            "spot_at_expiry": spot_at_expiry,
            "put_leg_pnl": put_pnl,
            "call_leg_pnl": call_pnl,
            "total_pnl": total_pnl,
        })

    return {
        "config": {
            "underlying": underlying,
            "expiry": expiry.isoformat(),
            "spot_at_entry": spot_at_entry,
            "put_short_strike": put_short_strike,
            "put_long_strike": put_long_strike,
            "call_short_strike": call_short_strike,
            "call_long_strike": call_long_strike,
            "days_to_expiry": days_to_expiry,
            "iv": iv,
            "lot_size": lot_size,
        },
        "legs": {
            "put_short": {"strike": put_short_strike, "premium": put_short},
            "put_long": {"strike": put_long_strike, "premium": put_long},
            "call_short": {"strike": call_short_strike, "premium": call_short},
            "call_long": {"strike": call_long_strike, "premium": call_long},
        },
        "iron_condor": {
            "net_credit": net_credit,
            "max_profit": max_profit,
            "max_loss": max_loss,
            "lower_breakeven": lower_breakeven,
            "upper_breakeven": upper_breakeven,
            "profit_zone": f"[{lower_breakeven:.1f}, {upper_breakeven:.1f}]",
        },
        "pnl_zones": pnl_zones,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--underlying", type=str, default="NIFTY")
    parser.add_argument("--expiry", type=lambda s: dt.date.fromisoformat(s), default=None)
    parser.add_argument("--spot", type=float, default=24000.0)
    parser.add_argument("--put-short", type=float, default=23500.0)
    parser.add_argument("--put-long", type=float, default=23300.0)
    parser.add_argument("--call-short", type=float, default=24500.0)
    parser.add_argument("--call-long", type=float, default=24700.0)
    parser.add_argument("--days-to-expiry", type=int, default=7)
    parser.add_argument("--iv", type=float, default=0.15)
    parser.add_argument("--lot-size", type=int, default=50)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(
        underlying=args.underlying,
        expiry=args.expiry,
        spot_at_entry=args.spot,
        put_short_strike=args.put_short,
        put_long_strike=args.put_long,
        call_short_strike=args.call_short,
        call_long_strike=args.call_long,
        days_to_expiry=args.days_to_expiry,
        iv=args.iv,
        lot_size=args.lot_size,
    )

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())