"""options/08_iron_condor_hedging: Iron condor with dynamic delta hedging.

Strategy: extend ``04_backtest_iron_condor.py`` — sell an OTM put spread and an
OTM call spread (4 legs, net credit), then delta-hedge the position intraday on
a simulated spot path: each bar, re-price the 4 legs with
``honba.analytics.greeks.bs_greeks`` (the same math as ``02_greeks_calculation``),
sum net delta/gamma/vega, and when ``|net delta|`` breaches the Greek risk bound
(0.25 underlying units by default) trade the underlying futures to flatten delta.
Gamma/vega breaches are logged but not hedged here (gamma scalping and vega
hedging are separate desks). Greeks are checked against
``honba_examples.options.GreekRiskBounds``.

Premium-side STT on the sell side of all four opening legs is modelled via
``honba_examples.options.option_sell_stt``; buying to close attracts none.
European, cash-settled index options via ICCL (T+1) — no exercise or delivery
leg. Runs are offline and deterministic on a seeded intraday path.

Run::

    python examples/options/08_iron_condor_hedging.py --spot 24000 --wings 200 --iv 0.15 --steps 72
    python examples/options/08_iron_condor_hedging.py --dte 7 --delta-bound 0.10 --gamma-bound 0.002 --out hedge.json

Pitfalls::

    - Delta hedging an iron condor is not risk-free: gamma jumps near the short
      strikes, vega (vol) risk is unhedged, and pin risk sits exactly where the
      hedge is least stable. This example hedges delta only.
    - Greeks come from flat-iv Black-Scholes: skew, smile and iv term structure
      are ignored, so the hedge ratio is only as good as the model.
    - The underlying futures hedge is assumed to fill at spot with zero cost, no
      freeze limits and no lot rounding; real futures have their own lot size,
      STT and margin.
    - The seeded GBM path has realised vol equal to implied vol, so it shows
      mechanics, not edge; real intraday option data is required for that.
    - STT is charged on the four sell legs at entry and sunk regardless of
      outcome; re-verify the rate against the latest SEBI/NSE circular.
    - Square-off at 15:20 IST is a hard model exit; broker MIS square-off is
      policy-dependent.
"""

from __future__ import annotations

import random
import sys
from typing import Any

from honba.analytics.greeks import bs_greeks, bs_price
from honba.domain.option import OptionKind

from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.options import (
    GreekRiskBounds,
    check_greek_bounds,
    option_sell_stt,
)

__all__ = ["main", "run"]

SESSION_OPEN_MINUTE = 9 * 60 + 15  # 09:15 IST
ENTRY_MINUTE = 9 * 60 + 20  # 09:20 IST
SQUARE_OFF_MINUTE = 15 * 60 + 20  # 15:20 IST
SESSION_CLOSE_MINUTE = 15 * 60 + 30  # 15:30 IST
SESSION_MINUTES = SESSION_CLOSE_MINUTE - SESSION_OPEN_MINUTE


def _simulate_intraday_path(
    spot_open: float, vol: float, steps: int, seed: int = 42
) -> list[float]:
    """Seeded GBM intraday path; re-seeded per call for determinism."""
    rng = random.Random(seed)
    dt_step = 1.0 / (steps * 365.0)
    path = [spot_open]
    for _ in range(steps - 1):
        z = rng.gauss(0.0, 1.0)
        path.append(path[-1] * (1.0 + vol * (dt_step**0.5) * z))
    return path


def run(
    underlying: str = "NIFTY",
    spot_at_entry: float = 24000.0,
    put_short_strike: float = 23500.0,
    put_long_strike: float = 23300.0,
    call_short_strike: float = 24500.0,
    call_long_strike: float = 24700.0,
    iv: float = 0.15,
    lot_size: int = 50,
    steps: int = 72,  # 5-min bars 09:20 -> 15:20
    dte: int = 7,  # days to expiry at entry
    delta_bound: float = 0.25,  # underlying units; hedge when |net delta| exceeds
    gamma_bound: float = 0.005,
    vega_bound: float = 0.50,
    seed: int = 42,
) -> dict[str, Any]:
    """Short iron condor opened at entry; delta-hedged intraday; square off at 15:20."""
    strikes = {
        "put_short": put_short_strike,
        "put_long": put_long_strike,
        "call_short": call_short_strike,
        "call_long": call_long_strike,
    }
    r = 0.07

    def tau_at(minute_of_session: int) -> float:
        """Years to expiry: whole days left plus the session fraction of today."""
        return max(
            1e-6,
            dte / 365.0 + (SESSION_MINUTES - minute_of_session) / (SESSION_MINUTES * 365.0),
        )

    tau_entry = tau_at(ENTRY_MINUTE - SESSION_OPEN_MINUTE)

    # Opening premiums (mid) and net credit received.
    prem = {
        leg: bs_price(
            OptionKind.PUT if leg.startswith("put") else OptionKind.CALL,
            spot_at_entry,
            strike,
            tau_entry,
            iv,
            r,
        )
        for leg, strike in strikes.items()
    }
    net_credit = (prem["put_short"] - prem["put_long"]) + (prem["call_short"] - prem["call_long"])

    # Sell-side premium STT on all four opening legs, sunk at entry.
    stt_total = float(
        option_sell_stt(prem["put_short"], lot_size)
        + option_sell_stt(prem["put_long"], lot_size)
        + option_sell_stt(prem["call_short"], lot_size)
        + option_sell_stt(prem["call_long"], lot_size)
    )

    path = _simulate_intraday_path(spot_at_entry, iv, steps, seed)
    bounds = GreekRiskBounds(max_net_delta=delta_bound, max_gamma=gamma_bound, max_vega=vega_bound)

    hedge_trades: list[dict[str, Any]] = []
    greek_snapshots: list[dict[str, Any]] = []
    breach_count = 0
    hedge_cash = 0.0  # cash from underlying hedges (buy -> negative, sell -> positive)

    last_spot = path[-1]
    last_step = len(path) - 1
    last_tau = tau_entry
    for i, spot in enumerate(path):
        step_abs_minute = ENTRY_MINUTE + i * ((SQUARE_OFF_MINUTE - ENTRY_MINUTE) / (last_step))
        step_minute = step_abs_minute - SESSION_OPEN_MINUTE
        tau = tau_at(step_minute)
        last_tau = tau

        # Position Greeks: short legs contribute negative of their Greeks.
        legs = {
            leg: bs_greeks(
                OptionKind.PUT if leg.startswith("put") else OptionKind.CALL,
                spot,
                strike,
                tau,
                iv,
                r,
            )
            for leg, strike in strikes.items()
        }
        net_delta = sum(
            (-1.0 if leg.endswith("_short") else 1.0) * g.delta for leg, g in legs.items()
        )
        net_gamma = sum(
            (-1.0 if leg.endswith("_short") else 1.0) * g.gamma for leg, g in legs.items()
        )
        net_vega = sum(
            (-1.0 if leg.endswith("_short") else 1.0) * g.vega for leg, g in legs.items()
        )

        greek_snapshots.append(
            {
                "step": i,
                "time": f"{int(step_abs_minute) // 60:02d}:{int(step_abs_minute) % 60:02d}",
                "spot": spot,
                "tau_days": tau * 365.0,
                "net_delta": net_delta,
                "net_gamma": net_gamma,
                "net_vega": net_vega,
            }
        )

        breaches = check_greek_bounds(net_delta, net_gamma, net_vega, bounds)
        breach_count += len(breaches)

        # Delta hedge: flatten net delta with the underlying futures (delta = 1 per unit).
        if abs(net_delta) > delta_bound:
            hedge_qty = round(-net_delta)  # opposite sign to flatten
            if hedge_qty != 0:
                hedge_cash -= hedge_qty * spot  # buy -> cash out, sell -> cash in
                hedge_trades.append(
                    {
                        "step": i,
                        "time": greek_snapshots[-1]["time"],
                        "spot": spot,
                        "net_delta_before": net_delta,
                        "hedge_qty": hedge_qty,
                        "cash_flow": -hedge_qty * spot,
                    }
                )

        # Hard square-off at 15:20 IST: close the loop at this step.
        if step_abs_minute >= SQUARE_OFF_MINUTE:
            last_spot = spot
            last_step = i
            break

    # Mark-to-model close at square-off: buy back the legs at current premiums.
    tau_exit = last_tau
    close_prem = {
        leg: bs_price(
            OptionKind.PUT if leg.startswith("put") else OptionKind.CALL,
            last_spot,
            strike,
            tau_exit,
            iv,
            r,
        )
        for leg, strike in strikes.items()
    }
    # Short legs: keep (open - close) premium per lot.
    leg_pnl = {leg: (prem[leg] - close_prem[leg]) * lot_size for leg in strikes}
    gross_pnl = sum(leg_pnl.values())
    net_pnl = gross_pnl - stt_total + hedge_cash

    return {
        "config": {
            "underlying": underlying,
            "spot_at_entry": spot_at_entry,
            "strikes": strikes,
            "iv": iv,
            "lot_size": lot_size,
            "steps": steps,
            "dte": dte,
            "delta_bound": delta_bound,
            "gamma_bound": gamma_bound,
            "vega_bound": vega_bound,
            "seed": seed,
        },
        "entry": {
            "time": "09:20",
            "premiums": prem,
            "net_credit": net_credit,
            "stt_total": stt_total,
            "breakeven_lower": put_short_strike - net_credit,
            "breakeven_upper": call_short_strike + net_credit,
        },
        "greeks": greek_snapshots,
        "risk": {
            "breach_count": breach_count,
            "final_net_delta": greek_snapshots[-1]["net_delta"] if greek_snapshots else 0.0,
            "final_net_gamma": greek_snapshots[-1]["net_gamma"] if greek_snapshots else 0.0,
            "final_net_vega": greek_snapshots[-1]["net_vega"] if greek_snapshots else 0.0,
        },
        "exit": {
            "time": "15:20",
            "spot": last_spot,
            "premiums": close_prem,
            "leg_pnl": leg_pnl,
        },
        "hedge": {
            "trades": hedge_trades,
            "trade_count": len(hedge_trades),
            "hedge_cash": hedge_cash,
        },
        "summary": {
            "net_credit": net_credit,
            "close_pnl": gross_pnl,
            "stt_total": stt_total,
            "hedge_cash": hedge_cash,
            "net_pnl": net_pnl,
            "return_on_credit_pct": (net_pnl / (net_credit * lot_size) * 100.0)
            if net_credit > 0
            else 0.0,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--underlying", type=str, default="NIFTY")
    parser.add_argument("--spot", type=float, default=24000.0)
    parser.add_argument("--put-short", type=float, default=23500.0)
    parser.add_argument("--put-long", type=float, default=23300.0)
    parser.add_argument("--call-short", type=float, default=24500.0)
    parser.add_argument("--call-long", type=float, default=24700.0)
    parser.add_argument("--wings", type=float, default=None)
    parser.add_argument("--iv", type=float, default=0.15)
    parser.add_argument("--lot-size", type=int, default=50)
    parser.add_argument("--steps", type=int, default=72)
    parser.add_argument("--dte", type=int, default=7)
    parser.add_argument("--delta-bound", type=float, default=0.25)
    parser.add_argument("--gamma-bound", type=float, default=0.005)
    parser.add_argument("--vega-bound", type=float, default=0.50)
    parser.add_argument("--seed", type=int, default=42)
    add_out_arg(parser)
    args = parser.parse_args(argv)

    if args.wings is not None:
        args.put_short = args.spot - args.wings
        args.put_long = args.spot - 2.0 * args.wings
        args.call_short = args.spot + args.wings
        args.call_long = args.spot + 2.0 * args.wings

    result = run(
        underlying=args.underlying,
        spot_at_entry=args.spot,
        put_short_strike=args.put_short,
        put_long_strike=args.put_long,
        call_short_strike=args.call_short,
        call_long_strike=args.call_long,
        iv=args.iv,
        lot_size=args.lot_size,
        steps=args.steps,
        dte=args.dte,
        delta_bound=args.delta_bound,
        gamma_bound=args.gamma_bound,
        vega_bound=args.vega_bound,
        seed=args.seed,
    )

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
