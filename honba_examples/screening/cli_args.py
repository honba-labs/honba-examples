"""honba_examples.screening.cli_args: argparse options for the chained screener -> SIP CLI.

One definition of the screener (``--strategy``, ``--graham-mode``, ``--train-years``, ``--top``,
``--screen-only``), SIP (``--sip``, ``--sip-mode``, ``--allocation``), and stream
(``--symbols``, ``--asof``, ``--end-date``, ``--out``) flags, shared by the chained script
and its ``09_forward_sip_backtest`` alias.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from honba_examples.screening.common import DEFAULT_MAX_STALE_DAYS

__all__ = ["add_chain_args", "add_nl_screener_args"]


def add_chain_args(
    parser: argparse.ArgumentParser,
    *,
    strategy: str = "multibagger",
    graham_mode: str = "defensive",
    train_years: int = 3,
    sip_mode: str = "portfolio",
    allocation: str = "52w-ratio",
    top: int = 5,
) -> None:
    """Add screener and SIP specific options alongside the ``HonbaExample`` base args."""
    parser.add_argument(
        "--strategy",
        choices=[
            "multibagger",
            "graham",
            "intelligent_investor",
            "net_net",
            "ncav",
            "dividend",
            "value",
            "all",
        ],
        default=strategy,
        help="Screener strategy: 'multibagger' (momentum), 'graham'/'intelligent_investor' (Benjamin Graham value), 'dividend', or 'value'",
    )
    parser.add_argument(
        "--graham-mode",
        choices=["defensive", "enterprising"],
        default=graham_mode,
        help="Graham strategy profile: 'defensive' (strict 22.5 Graham product, low D/E) or 'enterprising'",
    )
    parser.add_argument(
        "--asof",
        dest="asof_str",
        type=str,
        default=None,
        help="In-sample evaluation date (YYYY-MM-DD). If omitted, falls back to --start.",
    )
    parser.add_argument(
        "--end-date",
        dest="end_date_str",
        type=str,
        default=None,
        help="Terminal date for forward out-of-sample backtest (YYYY-MM-DD, alias for --end)",
    )
    parser.add_argument(
        "--train-years",
        type=int,
        default=train_years,
        help="Number of in-sample training years prior to asof date (default: 3)",
    )
    parser.add_argument(
        "--max-stale-days",
        type=int,
        default=DEFAULT_MAX_STALE_DAYS,
        help=(
            "Skip screened symbols whose last bar is more than N calendar days before --asof "
            "(delisted/suspended; default: 10, 0 disables)"
        ),
    )
    parser.add_argument(
        "--sip-amount",
        "--sip",
        dest="sip_amount",
        type=float,
        default=None,
        help="Monthly SIP installment in INR (default: ₹10,000 total portfolio SIP)",
    )
    parser.add_argument(
        "--sip-mode",
        dest="sip_mode",
        choices=["portfolio", "per-stock"],
        default=sip_mode,
        help="How to interpret --sip: 'portfolio' (default) or 'per-stock'",
    )
    parser.add_argument(
        "--allocation",
        choices=["52w-ratio", "equal", "score"],
        default=allocation,
        help="Portfolio capital allocation: '52w-ratio' (proximity to 52w high), 'equal', or 'score'",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=top,
        help="Limit screening candidates to top N (default: 5)",
    )
    parser.add_argument(
        "--screen-only",
        action="store_true",
        help="Run Stage 1 screening only (do not run forward out-of-sample backtest)",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help=(
            "Explicit comma-separated symbols (skips screening). Use '-' to read the symbol "
            "stream from stdin, or pipe a screener into this script to auto-detect."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional path to write JSON output report",
    )


def add_nl_screener_args(parser: argparse.ArgumentParser) -> None:
    """Add the natural-language screener options (``--query``, ``--asof``, ``--backend``, ...)."""
    parser.add_argument(
        "--query",
        default="market_cap > 25000Cr and close above 50-day moving average",
        help="Natural language screening query",
    )
    parser.add_argument(
        "--asof",
        default=None,
        help="Point-in-time evaluation date (YYYY-MM-DD), e.g. '2021-10-11' for 5 years ago",
    )
    parser.add_argument(
        "--lookback-years",
        type=int,
        default=None,
        help="Convenience lookback years (e.g. 5 for 5 years ago)",
    )
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="Run forward performance backtest from asof date to present",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "ollama", "rule-based", "fake"],
        default="auto",
        help="LLM translation backend",
    )
    parser.add_argument("--limit", type=int, default=20, help="Max results to display")
    parser.add_argument(
        "--format",
        choices=["auto", "table", "json"],
        default="auto",
        help="Output format",
    )
