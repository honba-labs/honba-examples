"""strategies/09_multibagger_momentum: pipeline recipe — multibagger screener → quality → SIP.

Composition via :mod:`honba_examples.pipeline` (``>>``/``|``):

  MultibaggerScreener (Stage 2 momentum + capital efficiency)
    → FundamentalQualityFilter (ROCE / Debt-Eq / margin)
    → FixedSipSizer (per-portfolio SIP)
    → SipBacktestHarness (forward XIRR + terminal value)

Mirror the CLI chain::

    python screening/01_multibagger_screener.py --asof 2025-03-01 --top 10 --format symbols \\
      | python backtesting/10_chained_screener_and_sip.py --asof 2025-03-01 --sip-amount 10000

Run the in-code pipeline::

    python strategies/09_multibagger_momentum.py --asof 2025-03-01 --top 10 --sip-amount 10000
    python strategies/09_multibagger_momentum.py --asof 2025-03-01 --top 10 --format json

Pitfalls::

    - Day-of-asof boundary: the screen uses bars up to and including --asof, while the harness buys
      at the open of the first session on or after it. The default 2025-03-01 is a Saturday, so the
      first buy is Monday's open and nothing leaks; for a weekday --asof the buy price precedes a
      close the screen already used.
    - Fundamentals dating: ROCE and debt/equity use rows with fiscal_date <= asof (period end, not
      publication date). FundamentalQualityFilter lets a candidate pass when ROCE or debt/equity is
      missing, so thin data counts as quality.
    - Survivorship: the screener ranks every symbol in the local catalog, i.e. what is listed and
      ingested today. In the simulation a name with no bars after the as-of date is skipped or held
      flat (sip.py), so collapses and delistings under-report.
    - Data mining: min 1-year return 15, within 15 percent of the 52-week high, debt/equity 1.5 and
      min ROCE 10 are hand-set thresholds. Re-running with variants until the forward XIRR looks
      good is a multiple-testing exercise; fix thresholds before looking and log how many were
      tried.
    - One draw, no friction: a single as-of, up to 10 names, a forward window that simply runs to the
      catalog's latest bar, price-only, no costs. Momentum screens also do very differently by regime; repeat
      over several as-of dates and compare with an index SIP.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from honba_examples.cli import make_parser
from honba_examples.pipeline import (
    FixedSipSizer,
    FundamentalQualityFilter,
    MultibaggerScreener,
    SipBacktestHarness,
)

__all__ = ["build_pipeline", "main", "run"]


def build_pipeline(
    asof: str = "2025-03-01",
    top: int = 10,
    sip_amount: float = 10000.0,
    min_roce: float = 10.0,
    max_debt_to_equity: float = 1.5,
    end_date: str | None = None,
    initial_capital: float | None = None,
) -> Any:
    return (
        MultibaggerScreener(
            asof=asof, max_pct_from_high=15.0, max_debt_to_equity=max_debt_to_equity, top=top
        )
        >> FundamentalQualityFilter(min_roce=min_roce, max_debt_to_equity=max_debt_to_equity)
        >> FixedSipSizer(monthly_amount=sip_amount)
        >> SipBacktestHarness(
            asof=asof, end_date=end_date, initial_capital=initial_capital, monthly_sip=sip_amount
        )
    )


def run(
    asof: str = "2025-03-01",
    top: int = 10,
    sip_amount: float = 10000.0,
    output_format: str = "table",
    initial_capital: float | None = None,
) -> Any:
    pipeline = build_pipeline(
        asof=asof, top=top, sip_amount=sip_amount, initial_capital=initial_capital
    )
    results = pipeline.run()
    if output_format == "json":
        print(
            json.dumps(
                {
                    "asof": asof,
                    "top": top,
                    "symbols": results.get("symbols"),
                    "candidates": results.get("candidates"),
                    "weights": results.get("weights"),
                    "summary": results.get("summary"),
                    "stocks": results.get("stocks"),
                },
                indent=2,
                default=str,
            )
        )
    else:
        results.print_summary()
        print(
            f"\nCandidates in: {len(results.get('candidates') or [])}  ->  out: {results.summary and results.summary.get('candidate_count')}"
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--asof", default="2025-03-01", help="screening date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="forward window end (YYYY-MM-DD)")
    parser.add_argument("--top", type=int, default=10, help="top-N multibaggers")
    parser.add_argument(
        "--sip-amount", type=float, default=10000.0, help="monthly SIP per portfolio (INR)"
    )
    parser.add_argument(
        "--capital", type=float, default=None, help="lump-sum initial capital (INR)"
    )
    parser.add_argument(
        "--format", dest="output_format", choices=["table", "json"], default="table"
    )
    args = parser.parse_args(argv)
    run(
        asof=args.asof,
        top=args.top,
        sip_amount=args.sip_amount,
        output_format=args.output_format,
        initial_capital=args.capital,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
