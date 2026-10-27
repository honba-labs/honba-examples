"""options/06_nifty_920_straddle: Intraday short straddle sold at 09:20 IST.

Strategy: sell the ATM NIFTY call and ATM put at the 09:20 IST open, manage each
leg with a 25 % stop-loss (buy-to-close when a short leg's premium appreciates
25 % vs its entry premium), and square off whatever remains at 15:20 IST (the
MIS-style auto-square-off window; exchange cut-off is 15:30 IST). It is the
short-volatility counterpart to the long straddle in ``03_backtest_straddle.py``
and is hand-rolled here — no ``Strategy`` subclass; runs are offline and
deterministic on a seeded intraday path.

The premium-side STT is modelled on the sell side of each leg using the
statutory rate from ``honba_examples.options`` (0.0625 % of premium per SEBI /
NSE circular in force for FY 2025-26; re-verify against the latest circular).
Buying to close attracts no premium STT; the entry STT is sunk regardless of
outcome. Index options are European and cash-settled through ICCL on T+1, so
there is no exercise STT or delivery leg in this model.

Run::

    python examples/options/06_nifty_920_straddle.py --underlying NIFTY --spot 22000 --iv 0.15
    python examples/options/06_nifty_920_straddle.py --underlying NIFTY --spot 22000 --dte 0 --steps 72 --out straddle.json

Pitfalls::

    - Short gamma is uncapped: when spot breaks a breakeven (strike +/- credit)
      the short straddle loses point for point; no hedge, roll or adjustment is
      modelled, so a single directional move can exceed the total credit many
      times over.
    - STT is charged on the sell side only and is sunk at entry: even a full
      stop-out does not refund it. The rate constant must be re-verified against
      the latest SEBI/NSE circular before any paper or live run.
    - Fills are at Black-Scholes mid: no bid/ask cross, slippage, partial fills
      or freeze-quantity order splits (large short strikes may need slicing —
      see ``07_large_qty_options_slicer.py``).
    - The intraday path is a seeded GBM whose realised vol equals the flat iv,
      so the result is noise around expected theta decay; real P&L needs real
      intraday quotes, bid/ask and the actual expiry calendar (holidays change
      the session and tau).
    - Square-off is treated as a hard 15:20 IST exit; a broker's MIS auto
      square-off is policy-dependent and may start earlier or use a different
      price (close vs last-traded).
    - ATM strike is rounded to the contract strike step (50 for NIFTY); the
      rounding changes moneyness and the credit, and lot size changes by
      circular — read both from the instrument master, never hard-code.
"""

from __future__ import annotations

import random
import sys
from typing import Any

from honba.analytics.greeks import bs_price
from honba.domain.option import OptionKind

from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.options import (
    ICCL_CLEARING_CORPORATION,
    SETTLEMENT_CYCLE_DAYS,
    freeze_limit_qty,
    option_sell_stt,
)

__all__ = ["main", "run"]

SESSION_OPEN_MINUTE = 9 * 60 + 15  # 09:15 IST NSE cash/F&O session open
ENTRY_TIME = "09:20"  # strategy entry (HH:MM)
SQUARE_OFF_TIME = "15:20"  # MIS-style auto square-off (HH:MM)
SESSION_CLOSE_MINUTE = 15 * 60 + 30  # 15:30 IST exchange close
SESSION_MINUTES = SESSION_CLOSE_MINUTE - SESSION_OPEN_MINUTE


def _hhmm_to_minute(hhmm: str) -> int:
    """Convert an ``"HH:MM"`` IST string to minutes after midnight."""
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


def _simulate_intraday_path(
    spot_open: float, vol: float, steps: int, seed: int = 42
) -> list[float]:
    """Intraday spot path (GBM), one step per slice of the entry→close window.

    Re-seeded per call so repeated runs are identical."""
    rng = random.Random(seed)
    dt_step = 1.0 / (steps * 365.0)  # daily fraction per step
    path = [spot_open]
    for _ in range(steps - 1):
        z = rng.gauss(0.0, 1.0)
        path.append(path[-1] * (1.0 + vol * (dt_step**0.5) * z))
    return path


def run(
    underlying: str = "NIFTY",
    spot_at_entry: float = 22000.0,
    strike: float | None = None,
    iv: float = 0.15,
    lot_size: int = 50,
    steps: int = 72,  # 5-min bars from 09:20 to 15:20
    stop_pct: float = 0.25,  # per-leg stop loss vs entry premium
    dte: int = 0,  # days to expiry at entry (0DTE intraday default)
    seed: int = 42,
) -> dict[str, Any]:
    """Sell ATM straddle at entry; per-leg 25% stop; square off at 15:20 IST."""
    expiry_tag = f"{dte}DTE"
    strike = strike or round(spot_at_entry / 50) * 50  # NIFTY strike step
    # Tau (years) decays through the session; on an expiry day most of a day
    # remains at 09:20. Floored at a tiny epsilon so bs_price never divides by
    # zero near the close.
    session_minutes = SESSION_MINUTES
    entry_minute_of_session = _hhmm_to_minute(ENTRY_TIME) - SESSION_OPEN_MINUTE
    tau_entry = dte / 365.0 + (session_minutes - entry_minute_of_session) / (
        session_minutes * 365.0
    )
    r = 0.07

    call_prem_entry = bs_price(OptionKind.CALL, spot_at_entry, strike, tau_entry, iv, r)
    put_prem_entry = bs_price(OptionKind.PUT, spot_at_entry, strike, tau_entry, iv, r)
    total_credit = call_prem_entry + put_prem_entry

    # Sell-side premium STT (sunk at entry, both legs)
    stt_call = float(option_sell_stt(call_prem_entry, lot_size))
    stt_put = float(option_sell_stt(put_prem_entry, lot_size))
    stt_total = stt_call + stt_put

    path = _simulate_intraday_path(spot_at_entry, iv, steps, seed)

    call_active = True
    put_active = True
    call_exit_price = None
    put_exit_price = None
    call_exit_step = None
    put_exit_step = None
    stop_events = []

    for i, spot in enumerate(path):
        step_minute = entry_minute_of_session + i * (
            (session_minutes - entry_minute_of_session) / (steps - 1)
        )
        step_abs_minute = SESSION_OPEN_MINUTE + step_minute
        step_minute_int = int(step_abs_minute)
        step_hhmm = f"{step_minute_int // 60:02d}:{step_minute_int % 60:02d}"
        tau = max(
            1e-6,
            dte / 365.0 + (session_minutes - step_minute) / (session_minutes * 365.0),
        )

        if call_active:
            call_prem = bs_price(OptionKind.CALL, spot, strike, tau, iv, r)
            if call_prem >= call_prem_entry * (1.0 + stop_pct):
                call_active = False
                call_exit_price = call_prem
                call_exit_step = i
                stop_events.append(
                    {
                        "leg": "CALL",
                        "step": i,
                        "time": step_hhmm,
                        "spot": spot,
                        "exit_premium": call_prem,
                        "stop_pct": stop_pct,
                    }
                )

        if put_active:
            put_prem = bs_price(OptionKind.PUT, spot, strike, tau, iv, r)
            if put_prem >= put_prem_entry * (1.0 + stop_pct):
                put_active = False
                put_exit_price = put_prem
                put_exit_step = i
                stop_events.append(
                    {
                        "leg": "PUT",
                        "step": i,
                        "time": step_hhmm,
                        "spot": spot,
                        "exit_premium": put_prem,
                        "stop_pct": stop_pct,
                    }
                )

        # Hard square-off at 15:20 IST: close any still-active leg at that step.
        squareoff_minute_of_session = _hhmm_to_minute(SQUARE_OFF_TIME) - SESSION_OPEN_MINUTE
        if (call_active or put_active) and step_minute >= squareoff_minute_of_session:
            if call_active:
                call_exit_price = bs_price(OptionKind.CALL, spot, strike, tau, iv, r)
                call_exit_step = i
                call_active = False
            if put_active:
                put_exit_price = bs_price(OptionKind.PUT, spot, strike, tau, iv, r)
                put_exit_step = i
                put_active = False
            break

    # Final P&L: short legs keep (entry - exit) premium; legs never closed use
    # the last path premium (worst-case marking at the close of the model).
    last_spot = path[-1]
    tau_last = max(1e-6, dte / 365.0)
    call_prem_last = bs_price(OptionKind.CALL, last_spot, strike, tau_last, iv, r)
    put_prem_last = bs_price(OptionKind.PUT, last_spot, strike, tau_last, iv, r)

    call_exit_price = call_prem_entry if call_exit_price is None else call_exit_price
    put_exit_price = put_prem_entry if put_exit_price is None else put_exit_price
    # If a leg stayed open to the end of the path, mark at the last premium.
    call_final_prem = call_prem_last if call_exit_step is None else call_exit_price
    put_final_prem = put_prem_last if put_exit_step is None else put_exit_price

    call_pnl = (call_prem_entry - call_final_prem) * lot_size
    put_pnl = (put_prem_entry - put_final_prem) * lot_size
    gross_pnl = call_pnl + put_pnl
    net_pnl = gross_pnl - stt_total

    breakeven_lower = strike - total_credit
    breakeven_upper = strike + total_credit

    return {
        "config": {
            "underlying": underlying,
            "expiry_tag": expiry_tag,
            "strike": strike,
            "spot_at_entry": spot_at_entry,
            "iv": iv,
            "lot_size": lot_size,
            "steps": steps,
            "stop_pct": stop_pct,
            "dte": dte,
            "clearing_corporation": ICCL_CLEARING_CORPORATION,
            "settlement_days": SETTLEMENT_CYCLE_DAYS,
        },
        "entry": {
            "time": ENTRY_TIME,
            "call_premium": call_prem_entry,
            "put_premium": put_prem_entry,
            "total_credit": total_credit,
            "breakeven_lower": breakeven_lower,
            "breakeven_upper": breakeven_upper,
            "freeze_limit_qty": freeze_limit_qty(underlying),
            "entry_time": ENTRY_TIME,
            "square_off_time": SQUARE_OFF_TIME,
        },
        "costs": {
            "stt_call": stt_call,
            "stt_put": stt_put,
            "stt_total": stt_total,
            "note": "premium-side STT on the sell side only (buyer pays none)",
        },
        "exits": {
            "call": {
                "active": call_active,
                "exit_step": call_exit_step,
                "exit_premium": call_final_prem,
                "pnl": call_pnl,
            },
            "put": {
                "active": put_active,
                "exit_step": put_exit_step,
                "exit_premium": put_final_prem,
                "pnl": put_pnl,
            },
            "stop_events": stop_events,
        },
        "summary": {
            "gross_pnl": gross_pnl,
            "stt_total": stt_total,
            "net_pnl": net_pnl,
            "return_on_credit_pct": (net_pnl / (total_credit * lot_size) * 100.0)
            if total_credit > 0
            else 0.0,
            "max_adverse_spot_move_pct": ((max(path) - min(path)) / spot_at_entry * 100.0),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--underlying", type=str, default="NIFTY")
    parser.add_argument("--spot", type=float, default=22000.0)
    parser.add_argument("--strike", type=float, default=None)
    parser.add_argument("--iv", type=float, default=0.15)
    parser.add_argument("--lot-size", type=int, default=50)
    parser.add_argument("--steps", type=int, default=72)
    parser.add_argument("--stop-pct", type=float, default=0.25)
    parser.add_argument("--dte", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(
        underlying=args.underlying,
        spot_at_entry=args.spot,
        strike=args.strike,
        iv=args.iv,
        lot_size=args.lot_size,
        steps=args.steps,
        stop_pct=args.stop_pct,
        dte=args.dte,
        seed=args.seed,
    )

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
