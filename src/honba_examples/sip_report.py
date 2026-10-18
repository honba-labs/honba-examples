"""CLI args and console/JSON output for the single-instrument SIP backtest (08)."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from typing import Any

from honba_examples.cli import add_out_arg
from honba_examples.jsonable import jsonable

__all__ = ["add_sip_args", "emit_sip_report"]


def add_sip_args(parser: argparse.ArgumentParser) -> None:
    """Register the flags of ``08_sip_buy_and_hold`` (including ``--out`` / ``--format``)."""
    parser.add_argument(
        "--symbol",
        "--instrument",
        dest="symbol",
        default="ALPHAETF",
        help="Instrument symbol (default: ALPHAETF)",
    )
    parser.add_argument("--exchange", default="NSE", help="Exchange code (default: NSE)")
    parser.add_argument(
        "--frequency",
        choices=["monthly", "weekly"],
        default="monthly",
        help="SIP frequency: monthly or weekly",
    )
    parser.add_argument(
        "--sip-amount",
        type=float,
        default=5000.0,
        help="SIP installment amount in INR (default: 5000.0)",
    )
    parser.add_argument(
        "--initial-capital",
        "--initial-corpus",
        "--capital",
        dest="initial_corpus",
        type=float,
        default=0.0,
        help="Initial capital / lump sum corpus invested on day 1 in INR (default: 0.0)",
    )
    parser.add_argument(
        "--sip-day", type=int, default=1, help="Day of month for monthly SIP (1-28, default: 1)"
    )
    parser.add_argument(
        "--sip-weekday",
        type=int,
        default=0,
        help="Weekday for weekly SIP (0=Mon..4=Fri, default: 0)",
    )
    parser.add_argument(
        "--sip-count",
        "--no-of-sip",
        dest="sip_count",
        type=int,
        default=None,
        help="Maximum number of installments",
    )
    parser.add_argument(
        "--start",
        type=lambda s: dt.date.fromisoformat(s),
        default=None,
        help="Start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end", type=lambda s: dt.date.fromisoformat(s), default=None, help="End date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--fee-rate", type=float, default=0.0005, help="Transaction fee rate (default: 0.0005)"
    )
    parser.add_argument(
        "--download", action="store_true", help="Download/fetch missing gaps before running"
    )
    add_out_arg(parser)
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )


def emit_sip_report(result: dict[str, Any], args: argparse.Namespace) -> None:
    """Write ``--out`` (if given) and print the JSON or the human summary table."""
    s = result["summary"]
    json_text = json.dumps(jsonable(result), indent=2)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json_text + "\n", encoding="utf-8")
        print(f"[Output] Wrote full report to {args.out}")

    if args.format == "json":
        print(json_text)
        return

    xirr_str = f"{s['xirr_pct']:+.2f}%" if s["xirr_pct"] is not None else "N/A"
    print("=" * 64)
    print(f"       SIP Backtest: {s['symbol']}.{s['exchange']} ({s['frequency'].capitalize()})")
    print("=" * 64)
    print(
        f"  Date Range          : {s['first_session']} to {s['last_session']} ({s['sessions_count']} sessions)"
    )
    print(f"  SIP Frequency       : {s['frequency'].capitalize()}")
    print(f"  SIP Installment     : ₹{s['sip_amount']:,.2f}")
    print(f"  Initial Corpus      : ₹{s['initial_corpus']:,.2f}")
    print(f"  Installments Paid   : {s['installments_count']}")
    print("-" * 64)
    print(f"  Total Invested      : ₹{s['total_invested']:,.2f}")
    print(f"  Total Units Held    : {s['total_units']:,.0f}")
    print(f"  Avg Purchase Price  : ₹{s['avg_purchase_price']:,.2f}")
    print(f"  Final Market Price  : ₹{s['final_market_price']:,.2f}")
    print(f"  Final Portfolio Val : ₹{s['final_portfolio_value']:,.2f}")
    print(f"  Net Profit / Loss   : ₹{s['net_pnl']:+,.2f} ({s['total_return_pct']:+.2f}%)")
    print("-" * 64)
    print(f"  CAGR (Time-Weighted): {s['cagr_pct']:+.2f}%")
    print(f"  XIRR (Annualized)   : {xirr_str}")
    print(f"  Max Drawdown        : {s['max_drawdown_pct']:.2f}%")
    print(f"  Transaction Fees    : ₹{s['total_fees_paid']:,.2f}")
    print("=" * 64)
