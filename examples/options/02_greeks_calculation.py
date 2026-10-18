"""options/02_greeks_calculation: Black-Scholes Greeks per contract.

Computes delta, gamma, theta, vega and rho for European options with
`honba.analytics.greeks.bs_greeks`, and builds the Greeks surface across strikes.
Teaches how each Greek varies with moneyness around an ATM reference. No `Strategy`
subclass — runs are offline and deterministic.

Run::

    python examples/options/02_greeks_calculation.py --spot 24000 --strike 24000 --days-to-expiry 7 --iv 0.15

Pitfalls::

    - Model assumptions: Black-Scholes prices European options on a non-dividend-paying underlying
      with a flat constant volatility and rate (r defaults to 0.07). Index options carry
      dividends/forward basis that this ignores, and early exercise (American style) is not modelled
      at all.
    - Flat vol: the same --iv prices every strike in the "surface", so it is a Greeks profile across
      strikes, not a volatility surface. A real smile gives each strike its own iv; plugging one iv
      into all of them mis-states OTM delta, gamma and vega.
    - Time and units: tau is days_to_expiry/365 in whole calendar days, so there is no time-of-day
      (the contract expires at the 15:30 IST close) and 0 days returns intrinsic value with zero
      gamma, vega and theta. Theta is per calendar day, vega per 1 vol point, rho per 1 percent
      rate; mixing these conventions with a broker's Greeks is a common source of off-by-100 errors.
    - Sensitivities, not forecasts: Greeks are local derivatives at one spot and iv. The labelled
      "atm_greeks" are for the --strike you pass, which is ATM only at the defaults, and delta or
      gamma will drift as spot, iv and time move.
"""

from __future__ import annotations

import sys
from typing import Any

from honba.analytics.greeks import bs_greeks
from honba.domain.option import OptionKind

from honba_examples.cli import add_out_arg, emit_json, make_parser

__all__ = ["main", "run"]


def run(
    spot: float = 24000.0,
    strike: float = 24000.0,
    days_to_expiry: int = 7,
    iv: float = 0.15,
    r: float = 0.07,
    kind: str = "CALL",
) -> dict[str, Any]:
    kind_enum = OptionKind.CALL if kind.upper() == "CALL" else OptionKind.PUT
    tau = days_to_expiry / 365.0

    greeks = bs_greeks(kind_enum, spot, strike, tau, iv, r)

    # Surface across strikes
    strikes = [strike + i * 50 for i in range(-5, 6)]
    surface = []
    for s in strikes:
        g = bs_greeks(kind_enum, spot, s, tau, iv, r)
        surface.append(
            {
                "strike": s,
                "delta": g.delta,
                "gamma": g.gamma,
                "theta": g.theta,
                "vega": g.vega,
                "rho": g.rho,
            }
        )

    return {
        "config": {
            "spot": spot,
            "strike": strike,
            "days_to_expiry": days_to_expiry,
            "iv": iv,
            "rate": r,
            "kind": kind,
        },
        "atm_greeks": {
            "delta": greeks.delta,
            "gamma": greeks.gamma,
            "theta": greeks.theta,
            "vega": greeks.vega,
            "rho": greeks.rho,
        },
        "surface": surface,
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--spot", type=float, default=24000.0)
    parser.add_argument("--strike", type=float, default=24000.0)
    parser.add_argument("--days-to-expiry", type=int, default=7)
    parser.add_argument("--iv", type=float, default=0.15)
    parser.add_argument("--rate", type=float, default=0.07)
    parser.add_argument("--kind", type=str, choices=["CALL", "PUT"], default="CALL")
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(
        spot=args.spot,
        strike=args.strike,
        days_to_expiry=args.days_to_expiry,
        iv=args.iv,
        r=args.rate,
        kind=args.kind,
    )

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
