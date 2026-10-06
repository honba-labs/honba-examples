"""options/02_greeks_calculation: Black-Scholes Greeks per contract.

Computes delta, gamma, theta, vega, rho for European options using
`honba.analytics.greeks.bs_greeks`. Demonstrates Greeks surface across strikes.

Run::

    python options/02_greeks_calculation.py --spot 24000 --strike 24000 --days-to-expiry 7 --iv 0.15
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

from honba.domain.option import OptionKind
from honba.analytics.greeks import bs_greeks
from honba_examples.jsonable import jsonable

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
        surface.append({
            "strike": s,
            "delta": g.delta,
            "gamma": g.gamma,
            "theta": g.theta,
            "vega": g.vega,
            "rho": g.rho,
        })

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
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--spot", type=float, default=24000.0)
    parser.add_argument("--strike", type=float, default=24000.0)
    parser.add_argument("--days-to-expiry", type=int, default=7)
    parser.add_argument("--iv", type=float, default=0.15)
    parser.add_argument("--rate", type=float, default=0.07)
    parser.add_argument("--kind", type=str, choices=["CALL", "PUT"], default="CALL")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(
        spot=args.spot,
        strike=args.strike,
        days_to_expiry=args.days_to_expiry,
        iv=args.iv,
        r=args.rate,
        kind=args.kind,
    )

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())