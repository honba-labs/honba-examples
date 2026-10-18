"""screening/07_mutual_fund_screener.py: Mutual Fund Screener with Equity DB Lookthrough.

Point-in-time mutual fund screener cross-referenced with equity holdings from fundamentals.duckdb:
- Category & Plan filtering (SEBI 2017 canonical categories, Direct vs Regular plans)
- Total Expense Ratio (TER) and AUM size filtering
- Equity Lookthrough: Weighted portfolio ROCE, weighted portfolio P/E, Large/Mid/Small cap breakdown
- Reverse Lookup: Screen funds holding a target equity symbol (e.g. RELIANCE, DIXON, INFY)
- Portfolio Overlap Exclusion: Filter out funds with high portfolio overlap against existing schemes

Emits a pure scheme-code/JSON stream on ``stdout`` and diagnostics on ``stderr`` for Unix pipe chaining.

Usage:
    # Screen top Direct Growth funds across all categories
    python screening/07_mutual_fund_screener.py --top 5

    # Screen Flexi Cap funds with high underlying business quality (Portfolio ROCE >= 15%)
    python screening/07_mutual_fund_screener.py --category "Equity:Flexi Cap" --min-portfolio-roce 15.0

    # Reverse Lookup: Find funds holding RELIANCE
    python screening/07_mutual_fund_screener.py --holding-symbol RELIANCE

    # Pipe scheme codes into the SIP backtester
    python screening/07_mutual_fund_screener.py --category "Equity:Flexi Cap" --format symbols | ...
Pitfalls::

    - Disclosure reporting lag: AMC portfolios are disclosed monthly by the 10th of the following
      month. The --disclosure-lag-days (default: 10) buffer prevents peeking at month-end holdings
      before their legal publication date.
    - Direct vs Regular plan drift: Regular plans include a 50-150 bps annual distributor drag that
      compounds to 10-25% lower terminal wealth over 20 years. Always specify --plan Direct for
      systematic quantitative allocation.
    - Holdings coverage limitations: Portfolio lookthrough maps domestic equity ISINs against the
      equity database; unlisted instruments, foreign equities, or derivative hedges will not match
      an NSE symbol and are reported as unclassified.
    - Survivorship and scheme mergers: AMCs frequently merge underperforming schemes into flagship
      funds (especially post-2017 SEBI categorisation circular), flattering historical survivorship.
    - AUM size penalty in small caps: Giant small-cap funds (>₹20,000 Cr) experience severe market
      impact and liquidity constraints, often diluting into large-cap names to deploy excess cash.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

from honba_examples.screener_cli import ScreenerCLIExample, rich_console
from honba_examples.screening import screen_mutual_funds
from honba_examples.screening.render import render_mutual_fund_table

__all__ = ["MutualFundScreenerExample", "main"]


class MutualFundScreenerExample(ScreenerCLIExample):
    """Point-in-time mutual fund screener with underlying equity lookthrough."""

    category: str = "all"
    plan: str = "Direct"
    option: str = "Growth"
    min_aum: float = 500.0
    max_aum: float | None = None
    max_ter: float = 1.0
    min_portfolio_roce: float | None = None
    max_portfolio_pe: float | None = None
    holding_symbol: str | None = None
    overlap_scheme: str | None = None
    max_overlap_pct: float | None = None
    disclosure_lag_days: int = 10
    top: int = 10

    def add_custom_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--category",
            type=str,
            default=self.category,
            help="SEBI Scheme Category (e.g. 'Equity:Flexi Cap', 'Equity:Large Cap', default: all)",
        )
        parser.add_argument(
            "--plan",
            type=str,
            default=self.plan,
            help="Plan type: Direct or Regular (default: Direct)",
        )
        parser.add_argument(
            "--option",
            type=str,
            default=self.option,
            help="Option type: Growth or IDCW (default: Growth)",
        )
        parser.add_argument(
            "--min-aum",
            type=float,
            default=self.min_aum,
            help="Minimum AUM in ₹ Crores (default: 500.0)",
        )
        parser.add_argument(
            "--max-aum",
            type=float,
            default=self.max_aum,
            help="Maximum AUM in ₹ Crores (default: disabled)",
        )
        parser.add_argument(
            "--max-ter",
            type=float,
            default=self.max_ter,
            help="Maximum Total Expense Ratio %% (default: 1.0)",
        )
        parser.add_argument(
            "--min-portfolio-roce",
            type=float,
            default=self.min_portfolio_roce,
            help="Minimum weighted portfolio ROCE %% from underlying equities (default: disabled)",
        )
        parser.add_argument(
            "--max-portfolio-pe",
            type=float,
            default=self.max_portfolio_pe,
            help="Maximum weighted portfolio P/E ratio from underlying equities (default: disabled)",
        )
        parser.add_argument(
            "--holding-symbol",
            type=str,
            default=self.holding_symbol,
            help="Filter funds holding this specific equity symbol (e.g. RELIANCE, DIXON)",
        )
        parser.add_argument(
            "--overlap-scheme",
            type=str,
            default=self.overlap_scheme,
            help="Reference scheme code to check overlap against (e.g. 122639)",
        )
        parser.add_argument(
            "--max-overlap-pct",
            type=float,
            default=self.max_overlap_pct,
            help="Maximum allowed portfolio overlap %% with reference scheme",
        )
        parser.add_argument(
            "--disclosure-lag-days",
            type=int,
            default=self.disclosure_lag_days,
            help="Reporting-lag buffer in days for monthly portfolio disclosures (default: 10)",
        )
        parser.add_argument(
            "--top", type=int, default=self.top, help="Limit to top N candidates (default: 10)"
        )

    def screen(self, catalog_dir: Path, asof: dt.date) -> list[dict[str, Any]]:
        overlap_spec = None
        if self.overlap_scheme and self.max_overlap_pct is not None:
            overlap_spec = (self.overlap_scheme, self.max_overlap_pct)

        candidates = screen_mutual_funds(
            asof_date=asof,
            category=self.category,
            plan=self.plan,
            option=self.option,
            min_aum_cr=self.min_aum if self.min_aum and self.min_aum > 0 else None,
            max_aum_cr=self.max_aum,
            max_ter=self.max_ter,
            min_portfolio_roce=self.min_portfolio_roce,
            max_portfolio_pe=self.max_portfolio_pe,
            holding_symbol=self.holding_symbol,
            max_overlap_with=overlap_spec,
            disclosure_lag_days=self.disclosure_lag_days,
            top=self.top,
        )

        # Set symbol key to scheme_code for Unix pipe compatibility
        for c in candidates:
            c["symbol"] = c["scheme_code"]

        return candidates

    def json_payload(self, candidates: list[dict[str, Any]], asof: dt.date) -> dict[str, Any]:
        return {
            "asof_date": asof.isoformat(),
            "strategy": "mutual_funds",
            "category": self.category,
            "plan": self.plan,
            "candidates": candidates,
        }

    def render_table(self, candidates: list[dict[str, Any]], asof: dt.date) -> None:
        console = rich_console()
        render_mutual_fund_table(
            candidates=candidates,
            asof_date=asof,
            category=self.category,
            console=console,
        )


def main(argv: list[str] | None = None) -> int:
    return MutualFundScreenerExample().main(argv)


if __name__ == "__main__":
    sys.exit(main())
