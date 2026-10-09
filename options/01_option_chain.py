"""options/01_option_chain: Load and explore option chain data.

Constructs a synthetic option chain for an index, filters by expiry, strike and
moneyness, and prints a summary table. Teaches the `OptionChain`/`OptionContract`
model used by the option strategy examples. No `Strategy` subclass — runs are
offline and deterministic on a synthetic chain.

Run::

    python options/01_option_chain.py --underlying NIFTY --expiry 2024-12-26
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

from honba.domain.instrument import InstrumentId, InstrumentKind
from honba.domain.option import OptionChain, OptionContract, OptionKind, OptionStyle

__all__ = ["main", "run"]


def _synthetic_chain(underlying: str, expiry: dt.date, spot: float = 24000.0, step: float = 50.0) -> OptionChain:
    """Create a synthetic option chain around ATM."""
    iid = InstrumentId(symbol=underlying, exchange="NSE", kind=InstrumentKind.INDEX)
    contracts = []

    # Generate strikes around spot
    atm_strike = round(spot / step) * step
    for offset in range(-10, 11):
        strike = atm_strike + offset * step
        for kind in (OptionKind.CALL, OptionKind.PUT):
            # Synthetic mid price: intrinsic + time value
            intrinsic = max(0.0, (spot - strike) if kind == OptionKind.CALL else (strike - spot))
            time_val = max(5.0, 100.0 - abs(offset) * 8.0)
            mid = intrinsic + time_val
            iv = 0.15 + abs(offset) * 0.005  # smile

            contracts.append(OptionContract(
                instrument_id=iid,
                expiry=expiry,
                strike=strike,
                kind=kind,
                style=OptionStyle.EUROPEAN,
                bid=mid - 1.0,
                ask=mid + 1.0,
                mid=mid,
                iv=iv,
                open_interest=1000 + abs(offset) * 500,
                volume=500 + abs(offset) * 200,
            ))

    return OptionChain(underlying=iid, expiry=expiry, spot=spot, contracts=contracts)


def _moneyness(kind: OptionKind, strike: float, spot: float) -> str:
    if kind == OptionKind.CALL:
        if strike < spot * 0.98:
            return "ITM"
        elif strike > spot * 1.02:
            return "OTM"
        return "ATM"
    else:  # PUT
        if strike > spot * 1.02:
            return "ITM"
        elif strike < spot * 0.98:
            return "OTM"
        return "ATM"


def run(
    underlying: str = "NIFTY",
    expiry: dt.date | None = None,
    spot: float = 24000.0,
    filter_moneyness: str | None = None,
) -> dict[str, Any]:
    expiry = expiry or dt.date(2024, 12, 26)
    chain = _synthetic_chain(underlying, expiry, spot)

    contracts = chain.contracts
    if filter_moneyness:
        contracts = [c for c in contracts if _moneyness(c.kind, c.strike, spot) == filter_moneyness]

    # Summary by strike
    strikes = sorted({c.strike for c in contracts})
    summary = []
    for strike in strikes:
        call = next((c for c in contracts if c.kind == OptionKind.CALL and c.strike == strike), None)
        put = next((c for c in contracts if c.kind == OptionKind.PUT and c.strike == strike), None)
        summary.append({
            "strike": strike,
            "moneyness": _moneyness(OptionKind.CALL, strike, spot) if call else _moneyness(OptionKind.PUT, strike, spot),
            "call": {"mid": call.mid, "iv": call.iv, "oi": call.open_interest} if call else None,
            "put": {"mid": put.mid, "iv": put.iv, "oi": put.open_interest} if put else None,
        })

    return {
        "config": {"underlying": underlying, "expiry": expiry.isoformat(), "spot": spot},
        "chain": {
            "underlying": chain.underlying.symbol,
            "expiry": chain.expiry.isoformat(),
            "spot": chain.spot,
            "contract_count": len(chain.contracts),
        },
        "filtered_count": len(contracts),
        "strikes": summary,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--underlying", type=str, default="NIFTY")
    parser.add_argument("--expiry", type=lambda s: dt.date.fromisoformat(s), default=None)
    parser.add_argument("--spot", type=float, default=24000.0)
    parser.add_argument("--filter-moneyness", type=str, choices=["ITM", "ATM", "OTM"], default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(
        underlying=args.underlying,
        expiry=args.expiry,
        spot=args.spot,
        filter_moneyness=args.filter_moneyness,
    )

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())