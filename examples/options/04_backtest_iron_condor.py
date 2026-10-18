"""options/04_backtest_iron_condor: Backtest an iron condor (4-leg credit spread).

Strategy: sell an OTM put spread and an OTM call spread, i.e. four legs total.
Teaches a defined-risk, range-bound payoff: max profit is the net credit, max loss
is the wider spread width minus the credit, with profit between the short strikes.
Legs are hand-rolled here — no `Strategy` subclass; runs are offline and
deterministic.

Run::

    python options/04_backtest_iron_condor.py --underlying NIFTY --expiry 2024-12-26

Pitfalls::

    - Payoff table, not a backtest: P&L is evaluated at fixed spots around entry with all four legs
      held to expiry and priced at one flat iv, so skew (richer OTM puts), IV changes and early
      management (stops, rolling at 50 percent of credit) are absent. A real iron condor's drawdown
      happens before expiry, on a mark-to-market basis.
    - Strikes are absolute levels (23500/23300/24500/24700) while --spot is separate. Moving --spot
      without moving the strikes silently changes the structure, and nothing checks that put_long <
      put_short < call_short < call_long, so a bad ordering gives a nonsense width, not an error.
    - max_loss is the wider wing minus the credit and holds only if all four legs fill at the model
      prices. Legging in or out, bid/ask on four legs and partial fills are not modelled; the credit
      is a theoretical mid and shrinks on four spreads. Brokerage, STT on exercised legs and margin
      are ignored (margin is far more than the credit and ties up capital).
    - Assignment and pin risk: the model is European and cash-settled, so there is no early
      assignment, but a short strike finishing close to spot at expiry is a binary outcome decided
      in the last minutes. A physically settled stock-option version carries delivery obligations
      that this does not.
"""

from __future__ import annotations

import datetime as dt
import sys
from typing import Any

from honba.analytics.greeks import bs_price
from honba.domain.option import OptionKind

from honba_examples.cli import add_out_arg, emit_json, make_parser

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
    for spot_at_expiry in [
        spot_at_entry - 800,
        spot_at_entry - 500,
        spot_at_entry - 300,
        put_short_strike,
        spot_at_entry,
        call_short_strike,
        spot_at_entry + 300,
        spot_at_entry + 500,
        spot_at_entry + 800,
    ]:
        put_short_payoff = max(0.0, put_short_strike - spot_at_expiry)
        put_long_payoff = max(0.0, put_long_strike - spot_at_expiry)
        call_short_payoff = max(0.0, spot_at_expiry - call_short_strike)
        call_long_payoff = max(0.0, spot_at_expiry - call_long_strike)

        put_pnl = (put_short - put_long - put_short_payoff + put_long_payoff) * lot_size
        call_pnl = (call_short - call_long - call_short_payoff + call_long_payoff) * lot_size
        total_pnl = put_pnl + call_pnl

        pnl_zones.append(
            {
                "spot_at_expiry": spot_at_expiry,
                "put_leg_pnl": put_pnl,
                "call_leg_pnl": call_pnl,
                "total_pnl": total_pnl,
            }
        )

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
    parser = make_parser(__doc__)
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
    add_out_arg(parser)
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

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
