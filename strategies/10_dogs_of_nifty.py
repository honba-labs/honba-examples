"""strategies/10_dogs_of_nifty: pipeline recipe — high dividend yielders at the 52-week low.

Composition via :mod:`honba_examples.pipeline` (``>>``/``|``):

  DividendYieldScreener (near 52w low, trailing yield ≥ 3%)
    → FundamentalQualityFilter (ROCE / Debt-Eq / margin)
    → FixedSipSizer (per-portfolio SIP)
    → SipBacktestHarness (forward XIRR + terminal value)

Mirror the CLI "Dogs of the NSE/ Dogs-of-Dow" chain::

    python screening/03_dividend_yield_screener.py --asof 2025-03-01 --min-div-yield 3.0 --max-pct-from-52w-low 15.0 --format symbols \\
      | python backtesting/10_chained_screener_and_sip.py --asof 2025-03-01 --sip-amount 10000

Or, in code::

    python strategies/10_dogs_of_nifty.py --asof 2025-03-01 --min-yield 3.0 --top 10 --sip-amount 10000

Pitfalls::

    - Not the classic Dogs of the Dow: that picks the highest-yielding index members on one date
      each year and rebalances annually. This recipe screens the catalog for trailing yield >= the
      minimum and price near the 52-week low, then buy-and-holds with a SIP. The name is a label.
    - Yield trap and missing income: trailing dividend over the as-of close rises when the price
      falls, so the screen favours names whose payout may be about to be cut and cannot see that.
      The forward simulation values shares at price only, so dividends received are not counted.
    - The regime guard is unwired from the CLI: --benchmark is parsed but main() never passes it to
      run(), so MarketRegimeFilter is only reachable via build_pipeline(benchmark=...). Even then it
      passes through with a warning when benchmark data is missing, so it can be silently off.
    - As-of hygiene: the screen reads bars up to and including --asof but the harness buys at that
      session's open (the default Saturday avoids it), fundamentals are filtered by fiscal_date not
      publication date, and the symbol set is today's catalog. Missing ROCE or debt/equity passes
      the quality filter.
    - High-yield value names often cluster in a few sectors and move together. With one as-of, about
      10 names and no costs, the result mostly reflects the sector and the regime after that date.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba_examples.cli import make_parser
from honba_examples.pipeline import (
    DividendYieldScreener,
    FixedSipSizer,
    FundamentalQualityFilter,
    MarketRegimeFilter,
    SipBacktestHarness,
)

__all__ = ["build_pipeline", "main", "run"]


def build_pipeline(
    asof: str = "2025-03-01",
    top: int = 10,
    min_yield_pct: float = 3.0,
    min_roce: float = 12.0,
    max_debt_to_equity: float = 1.0,
    max_pct_from_52w_low: float = 15.0,
    sip_amount: float = 10000.0,
    end_date: str | None = None,
    benchmark: str | None = None,
    initial_capital: float | None = None,
) -> Any:
    steps: list[Any] = [
        DividendYieldScreener(
            asof=asof,
            min_yield_pct=min_yield_pct,
            max_pct_from_52w_low=max_pct_from_52w_low,
            top=top,
        ),
        FundamentalQualityFilter(min_roce=min_roce, max_debt_to_equity=max_debt_to_equity),
    ]
    if benchmark:
        steps.append(MarketRegimeFilter(benchmark=benchmark, condition="above_sma200"))
    steps.append(FixedSipSizer(monthly_amount=sip_amount))
    steps.append(
        SipBacktestHarness(
            asof=asof, end_date=end_date, initial_capital=initial_capital, monthly_sip=sip_amount
        )
    )
    # Chain with >> left-fold.
    pipeline: Any = steps[0]
    for step in steps[1:]:
        pipeline = pipeline >> step
    return pipeline


def run(
    asof: str = "2025-03-01",
    top: int = 10,
    min_yield_pct: float = 3.0,
    sip_amount: float = 10000.0,
    output_format: str = "table",
    initial_capital: float | None = None,
    end_date: str | None = None,
    benchmark: str | None = None,
) -> Any:
    pipeline = build_pipeline(
        asof=asof,
        top=top,
        min_yield_pct=min_yield_pct,
        sip_amount=sip_amount,
        end_date=end_date,
        benchmark=benchmark,
        initial_capital=initial_capital,
    )
    results = pipeline.run()
    if output_format == "json":
        print(
            json.dumps(
                {
                    "asof": asof,
                    "symbols": results.get("symbols"),
                    "candidates": results.get("candidates"),
                    "weights": results.get("weights"),
                    "regime": results.get("regime"),
                    "summary": results.get("summary"),
                    "stocks": results.get("stocks"),
                },
                indent=2,
                default=str,
            )
        )
    else:
        regime = results.get("regime") or {}
        if regime.get("reason"):
            print(f"[regime] {regime.get('reason')}")
        results.print_summary()
        print(
            f"\nCandidates: {len(results.get('candidates') or [])} -> backtest: {results.summary and results.summary.get('candidate_count')}"
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--asof", default="2025-03-01", help="screening date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="forward window end (YYYY-MM-DD)")
    parser.add_argument("--top", type=int, default=10, help="top-N")
    parser.add_argument(
        "--min-yield", type=float, default=3.0, help="minimum trailing dividend yield %%"
    )
    parser.add_argument(
        "--sip-amount", type=float, default=10000.0, help="monthly SIP per portfolio (INR)"
    )
    parser.add_argument(
        "--capital", type=float, default=None, help="lump-sum initial capital (INR)"
    )
    parser.add_argument(
        "--benchmark", type=str, default=None, help="optional regime guard (e.g. NIFTY 50)"
    )
    parser.add_argument(
        "--format", dest="output_format", choices=["table", "json"], default="table"
    )
    args = parser.parse_args(argv)
    run(
        asof=args.asof,
        top=args.top,
        min_yield_pct=args.min_yield,
        sip_amount=args.sip_amount,
        output_format=args.output_format,
        initial_capital=args.capital,
        end_date=args.end_date,
        benchmark=args.benchmark,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
