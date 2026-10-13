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
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
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
