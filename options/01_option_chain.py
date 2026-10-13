"""options/01_option_chain: Load and explore option chain data.

Constructs a synthetic option chain for an index, filters by expiry, strike and
moneyness, and prints a summary table. Teaches the `OptionChain`/`OptionContract`
model used by the option strategy examples. No `Strategy` subclass — runs are
offline and deterministic on a synthetic chain.

Run::

    python options/01_option_chain.py --underlying NIFTY --expiry 2024-12-26
    python options/01_option_chain.py --format json
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba.domain.instrument import InstrumentId
from honba.domain.option import OptionChain, OptionContract, OptionKind, OptionStyle

from honba_examples.cli import add_out_arg, emit_json, make_parser

__all__ = ["main", "run"]


def _synthetic_chain(
    underlying: str, expiry: dt.date, spot: float = 24000.0, step: float = 50.0
) -> OptionChain:
    """Create a synthetic option chain around ATM."""
    iid = InstrumentId(symbol=underlying, exchange="NSE")
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

            contracts.append(
                OptionContract(
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
                )
            )

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
        call = next(
            (c for c in contracts if c.kind == OptionKind.CALL and c.strike == strike), None
        )
        put = next((c for c in contracts if c.kind == OptionKind.PUT and c.strike == strike), None)
        summary.append(
            {
                "strike": strike,
                "moneyness": _moneyness(OptionKind.CALL, strike, spot)
                if call
                else _moneyness(OptionKind.PUT, strike, spot),
                "call": {"mid": call.mid, "iv": call.iv, "oi": call.open_interest}
                if call
                else None,
                "put": {"mid": put.mid, "iv": put.iv, "oi": put.open_interest} if put else None,
            }
        )

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
    parser = make_parser(__doc__)
    parser.add_argument("--underlying", type=str, default="NIFTY")
    parser.add_argument("--expiry", type=lambda s: dt.date.fromisoformat(s), default=None)
    parser.add_argument("--spot", type=float, default=24000.0)
    parser.add_argument("--filter-moneyness", type=str, choices=["ITM", "ATM", "OTM"], default=None)
    add_out_arg(parser)
    parser.add_argument(
        "--format",
        choices=["auto", "table", "json"],
        default="auto",
        help="output format (default auto: rich table in interactive terminal, json for pipes/exchange)",
    )
    args = parser.parse_args(argv)

    result = run(
        underlying=args.underlying,
        expiry=args.expiry,
        spot=args.spot,
        filter_moneyness=args.filter_moneyness,
    )

    is_table = (args.format == "table") or (
        args.format == "auto" and sys.stdout.isatty() and not args.out
    )

    if is_table:
        from honba.display import Column, render_kv, render_table

        chain_info = result.get("chain", {})
        render_kv(
            [
                ("Underlying", chain_info.get("underlying")),
                ("Expiry", chain_info.get("expiry")),
                ("Spot Price", f"₹{chain_info.get('spot', 0):,.2f}"),
                (
                    "Contracts",
                    f"{result.get('filtered_count')} / {chain_info.get('contract_count')}",
                ),
            ],
            title="Option Chain Overview",
        )

        rows = []
        for s in result.get("strikes", []):
            call = s.get("call") or {}
            put = s.get("put") or {}
            rows.append(
                {
                    "call_oi": f"{call.get('oi', 0):,}" if call else "-",
                    "call_iv": f"{call.get('iv', 0) * 100:.1f}%"
                    if call and call.get("iv")
                    else "-",
                    "call_mid": f"₹{call.get('mid', 0):.2f}"
                    if call and call.get("mid") is not None
                    else "-",
                    "strike": f"{s['strike']:,.0f}",
                    "moneyness": s.get("moneyness", "-"),
                    "put_mid": f"₹{put.get('mid', 0):.2f}"
                    if put and put.get("mid") is not None
                    else "-",
                    "put_iv": f"{put.get('iv', 0) * 100:.1f}%" if put and put.get("iv") else "-",
                    "put_oi": f"{put.get('oi', 0):,}" if put else "-",
                }
            )

        cols = [
            Column("call_oi", "Call OI", align="right", style="cyan"),
            Column("call_iv", "Call IV", align="right", style="cyan"),
            Column("call_mid", "Call Mid", align="right", style="green"),
            Column("strike", "Strike", align="center", style="bold yellow"),
            Column("moneyness", "Type", align="center", style="white"),
            Column("put_mid", "Put Mid", align="right", style="red"),
            Column("put_iv", "Put IV", align="right", style="magenta"),
            Column("put_oi", "Put OI", align="right", style="magenta"),
        ]
        render_table(
            rows,
            cols,
            title=f"Option Chain: {chain_info.get('underlying')} ({chain_info.get('expiry')})",
        )
    else:
        emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
