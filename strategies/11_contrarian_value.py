"""strategies/11_contrarian_value: pipeline recipe — contrarian value at the 52-week low.

Composition via :mod:`honba_examples.pipeline` (``>>``/``|``):

  ValueScreener (near 52w low, P/E ≤ 20, P/B ≤ 2, ROCE ≥ 12, D/E ≤ 1.0)
    → FundamentalQualityFilter (ROCE / Debt-Eq / margin)
    → FixedSipSizer (per-portfolio SIP; swap for InverseVolatilitySizer to tilt)
    → SipBacktestHarness (forward XIRR + terminal value)

Mirror the CLI chain::

    python screening/02_value_52w_low_screener.py --asof 2025-03-01 --max-pe 20 --max-pb 2.0 --max-debt-equity 1.0 --format symbols \\
      | python backtesting/10_chained_screener_and_sip.py --asof 2025-03-01 --sip-amount 10000

Roadmap note (§3 "Risk Manager"): the ATR trailing-stop leg is a timing signal.  It is wired
as a signal-step stub in this recipe (pass-through); a future phase replaces it with the
event-driven harness after the backtest engine lands.  The overall done criterion is that the
pipeline itself — filters and all — runs end-to-end (it does), not that the trailing stop
trims positions inside the backtest yet.

Run::

    python strategies/11_contrarian_value.py --asof 2025-03-01 --top 10 --sip-amount 10000
    python strategies/11_contrarian_value.py --asof 2025-03-01 --top 10 --sizer inv_vol --format json

Pitfalls::

    - Cheap for a reason: P/E, P/B and ROCE are the latest statement dated on or before --asof
      (period end) combined with the as-of close, near the 52-week low. Trailing earnings at a
      cyclical peak or a stale year-old statement look cheap, and falling knives keep falling.
    - The risk leg is a stub: BuyAndHoldSignal passes everything through (the text says the ATR
      trailing-stop is not wired), so there is no exit rule. The result is buy-and-hold with monthly
      buys, not a risk-managed strategy, and drawdowns are not limited.
    - Stacked hand-set thresholds: max P/E 20, max P/B 2, ROCE 12, debt/equity 1.0, within 15
      percent of the low, then ROCE and debt/equity again in the quality filter (which lets missing
      values pass). Tuning these against the forward window you then report is in-sample; fix them
      first.
    - As-of boundary and survivorship: the screen uses --asof's close while the harness buys at that
      session's open (the default Saturday avoids it), and the universe is whatever is in today's
      catalog. Names with no later bars are skipped or held flat in the simulation.
    - Regime and sample: value near lows does badly in persistent downtrends and well in rebounds,
      so the answer depends on the as-of month. One as-of, about 10 names, price-only and no costs:
      repeat over several dates and compare with an index.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba_examples.cli import make_parser
from honba_examples.pipeline import (
    BuyAndHoldSignal,
    FixedSipSizer,
    FundamentalQualityFilter,
    InverseVolatilitySizer,
    SipBacktestHarness,
    ValueScreener,
)

__all__ = ["build_pipeline", "main", "run"]


def build_pipeline(
    asof: str = "2025-03-01",
    top: int = 10,
    max_pe: float | None = 20.0,
    max_pb: float | None = 2.0,
    max_pct_from_52w_low: float = 15.0,
    min_roce: float = 12.0,
    max_debt_to_equity: float = 1.0,
    sip_amount: float = 10000.0,
    end_date: str | None = None,
    sizer: str = "fixed_sip",
    initial_capital: float | None = None,
) -> Any:
    sizer_step: Any
    sizer_step = (
        InverseVolatilitySizer(vol_window=30)
        if sizer == "inv_vol"
        else FixedSipSizer(monthly_amount=sip_amount)
    )
    return (
        ValueScreener(
            asof=asof,
            max_pe=max_pe,
            max_pb=max_pb,
            max_pct_from_52w_low=max_pct_from_52w_low,
            min_roce=min_roce,
            max_debt_equity=max_debt_to_equity,
            top=top,
        )
        >> FundamentalQualityFilter(min_roce=min_roce, max_debt_to_equity=max_debt_to_equity)
        >> BuyAndHoldSignal()
        >> sizer_step
        >> SipBacktestHarness(
            asof=asof, end_date=end_date, initial_capital=initial_capital, monthly_sip=sip_amount
        )
    )


def run(
    asof: str = "2025-03-01",
    top: int = 10,
    sip_amount: float = 10000.0,
    output_format: str = "table",
    sizer: str = "fixed_sip",
    initial_capital: float | None = None,
) -> Any:
    pipeline = build_pipeline(
        asof=asof, top=top, sip_amount=sip_amount, sizer=sizer, initial_capital=initial_capital
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
            f"\nCandidates: {len(results.get('candidates') or [])} -> backtest: {results.summary and results.summary.get('candidate_count')}"
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--asof", default="2025-03-01", help="screening date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="forward window end (YYYY-MM-DD)")
    parser.add_argument("--top", type=int, default=10, help="top-N")
    parser.add_argument(
        "--sip-amount", type=float, default=10000.0, help="monthly SIP per portfolio (INR)"
    )
    parser.add_argument(
        "--capital", type=float, default=None, help="lump-sum initial capital (INR)"
    )
    parser.add_argument(
        "--sizer",
        choices=["fixed_sip", "inv_vol"],
        default="fixed_sip",
        help="allocation (default: fixed_sip)",
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
        sizer=args.sizer,
        initial_capital=args.capital,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
