"""screening/04_intelligent_investor_screener.py: Benjamin Graham Value Screener.

Point-in-time value screener based on Benjamin Graham's *The Intelligent Investor*:
- Graham Number = sqrt(22.5 * EPS * BVPS)
- Margin of Safety = (Graham Number - Price) / Graham Number * 100
- Valuation discipline: P/E <= 15-20, P/B <= 1.5-2.0, P/E * P/B <= 22.5
- Financial condition: Low Debt-to-Equity, robust Current Ratio, positive earnings and dividends

Emits a pure symbol/JSON stream on ``stdout`` and diagnostics on ``stderr`` for Unix pipe chaining.

Usage:
    # Screen top 5 Graham defensive value candidates as of 2024-01-01
    python screening/04_intelligent_investor_screener.py --asof 2024-01-01 --mode defensive --top 5

    # Pipe symbols into the forward SIP backtester
    python screening/04_intelligent_investor_screener.py --asof 2024-01-01 --format symbols \\
        | python backtesting/10_chained_screener_and_sip.py --asof 2024-01-01 --sip-amount 10000

Pitfalls::

    - A loose adaptation of Graham, not his tests: EPS and book value per share come from the
      single latest FY row, where Graham averaged earnings over several years and required
      long earnings and dividend records. One good or one bad year moves the Graham Number a lot.
    - Per-share figures use diluted average shares for the year, not shares at the balance-sheet
      date, so buybacks and issues distort book value per share, P/B and the margin of safety.
      Financials and capital-intensive firms are not treated separately: debt/equity and current
      ratio mean little for banks.
    - Mode overrides your flags: in --mode defensive the thresholds are fixed in code (P/E 15,
      P/B 1.5, P/E x P/B 22.5, debt/equity 0.6) and --max-pe, --max-pb and --max-debt-equity are
      ignored. A missing debt/equity, current assets or current liabilities passes its test.
    - Period end, not publication date: fundamentals use fiscal_date <= --asof (no filing-date
      column exists); --fiscal-lag-days defaults to 0. The ratios and statements tables each pick
      their own latest fiscal_date, so EPS and share count can come from different years.
    - Survivorship and lookahead: delisted value traps are absent from the catalog, and chaining
      to the SIP backtest buys at the --asof open after reading that day's close.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba_examples.screener_cli import ScreenerCLIExample, rich_console
from honba_examples.screening import screen_intelligent_investor

__all__ = ["IntelligentInvestorScreenerExample", "main"]


class IntelligentInvestorScreenerExample(ScreenerCLIExample):
    """Benjamin Graham value screener (Defensive & Enterprising investor profiles)."""

    train_years: int = 3
    mode: str = "defensive"
    min_margin_of_safety: float = 0.0
    max_graham_product: float = 22.5
    max_pe: float = 20.0
    max_pb: float = 2.0
    max_debt_equity: float = 1.0
    min_current_ratio: float = 1.2
    min_div_yield: float = 0.0
    require_positive_fcf: bool = False
    fiscal_lag_days: int = 0
    max_stale_days: int = 10
    top: int = 10

    def add_custom_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--train-years",
            type=int,
            default=self.train_years,
            help="In-sample training years prior to --asof (default: 3)",
        )
        parser.add_argument(
            "--mode",
            type=str,
            choices=["defensive", "enterprising"],
            default=self.mode,
            help="Graham profile: 'defensive' (strict 22.5 Graham product, low D/E) or 'enterprising' (default: defensive)",
        )
        parser.add_argument(
            "--min-margin-of-safety",
            type=float,
            default=self.min_margin_of_safety,
            help="Minimum Margin of Safety %% relative to Graham Number (default: 0.0)",
        )
        parser.add_argument(
            "--max-graham-product",
            type=float,
            default=self.max_graham_product,
            help="Maximum Graham product P/E * P/B (default: 22.5)",
        )
        parser.add_argument(
            "--max-pe",
            type=float,
            default=self.max_pe,
            help="Maximum derived P/E ratio (default: 20.0, use 0 to disable)",
        )
        parser.add_argument(
            "--max-pb",
            type=float,
            default=self.max_pb,
            help="Maximum derived P/B ratio (default: 2.0, use 0 to disable)",
        )
        parser.add_argument(
            "--max-debt-equity",
            type=float,
            default=self.max_debt_equity,
            help="Maximum debt-to-equity ratio (default: 1.0)",
        )
        parser.add_argument(
            "--min-current-ratio",
            type=float,
            default=self.min_current_ratio,
            help="Minimum current assets / current liabilities (default: 1.2, use 0 to disable)",
        )
        parser.add_argument(
            "--min-div-yield",
            type=float,
            default=self.min_div_yield,
            help="Minimum dividend yield %% (default: 0.0)",
        )
        parser.add_argument(
            "--require-positive-fcf",
            action=argparse.BooleanOptionalAction,
            default=self.require_positive_fcf,
            help="Require positive free cash flow (default: off)",
        )
        parser.add_argument(
            "--fiscal-lag-days",
            type=int,
            default=self.fiscal_lag_days,
            help="Reporting-lag buffer in days to avoid lookahead (default: 0)",
        )
        parser.add_argument(
            "--max-stale-days",
            type=int,
            default=self.max_stale_days,
            help=(
                "Skip symbols whose last bar is more than N calendar days before --asof "
                "(delisted/suspended; default: 10, 0 disables)"
            ),
        )
        parser.add_argument(
            "--top", type=int, default=self.top, help="Limit to top N candidates (default: 10)"
        )

    def screen(self, catalog_dir: Path, asof: dt.date) -> list[dict[str, Any]]:
        return screen_intelligent_investor(
            catalog_dir=catalog_dir,
            asof_date=asof,
            train_years=self.train_years,
            mode=self.mode,
            min_margin_of_safety=self.min_margin_of_safety,
            max_graham_product=self.max_graham_product,
            max_pe=self.max_pe if self.max_pe and self.max_pe > 0 else None,
            max_pb=self.max_pb if self.max_pb and self.max_pb > 0 else None,
            max_debt_equity=self.max_debt_equity
            if self.max_debt_equity and self.max_debt_equity > 0
            else None,
            min_current_ratio=self.min_current_ratio,
            min_div_yield=self.min_div_yield,
            require_positive_fcf=self.require_positive_fcf,
            fiscal_lag_days=self.fiscal_lag_days,
            max_stale_days=self.max_stale_days,
            top=self.top,
        )

    def json_payload(self, candidates: list[dict[str, Any]], asof: dt.date) -> dict[str, Any]:
        return {
            "asof_date": asof.isoformat(),
            "strategy": "intelligent_investor",
            "mode": self.mode,
            "train_years": self.train_years,
            "candidates": candidates,
        }

    def render_table(self, candidates: list[dict[str, Any]], asof: dt.date) -> None:
        from rich import box
        from rich.table import Table

        title = (
            f"Stage 1 (In-Sample): Benjamin Graham '{self.mode.title()}' Value Candidates "
            f"as of {asof} (P/E×P/B ≤ {self.max_graham_product:.1f})"
        )
        table = Table(title=title, border_style="cyan", box=box.ROUNDED, header_style="bold cyan")
        table.add_column("Symbol", style="bold cyan", no_wrap=True)
        table.add_column("Company", style="dim")
        table.add_column("Price (₹)", justify="right", no_wrap=True)
        table.add_column("EPS (₹)", justify="right", style="green", no_wrap=True)
        table.add_column("BVPS (₹)", justify="right", style="dim", no_wrap=True)
        table.add_column("P/E", justify="right", no_wrap=True)
        table.add_column("P/B", justify="right", no_wrap=True)
        table.add_column("P/E×P/B", justify="right", style="yellow", no_wrap=True)
        table.add_column("Graham Val (₹)", justify="right", style="bold yellow", no_wrap=True)
        table.add_column("Margin of Safety", justify="right", style="bold green", no_wrap=True)
        table.add_column("Cur Ratio", justify="right", no_wrap=True)
        table.add_column("Debt/Eq", justify="right", no_wrap=True)
        table.add_column("Div Yield", justify="right", style="magenta", no_wrap=True)

        for c in candidates:
            pe_s = f"{c['pe_ratio']:.1f}" if c.get("pe_ratio") is not None else "-"
            pb_s = f"{c['pb_ratio']:.2f}" if c.get("pb_ratio") is not None else "-"
            gp_s = f"{c['graham_product']:.1f}" if c.get("graham_product") is not None else "-"
            cr_s = f"{c['current_ratio']:.2f}" if c.get("current_ratio") is not None else "-"
            de_s = f"{c['debt_to_equity']:.2f}" if c.get("debt_to_equity") is not None else "-"
            div_s = f"{c['div_yield_pct']:.2f}%" if c.get("div_yield_pct", 0) > 0 else "-"
            mos_s = (
                f"+{c['margin_of_safety_pct']:.1f}%"
                if c.get("margin_of_safety_pct", 0) >= 0
                else f"{c['margin_of_safety_pct']:.1f}%"
            )

            table.add_row(
                c["symbol"],
                c.get("company_name", c["symbol"]),
                f"₹{c['close']:,.2f}",
                f"₹{c['eps']:,.2f}",
                f"₹{c['bvps']:,.2f}",
                pe_s,
                pb_s,
                gp_s,
                f"₹{c['graham_num']:,.2f}",
                mos_s,
                cr_s,
                de_s,
                div_s,
            )
        rich_console().print(table)


def main(argv: list[str] | None = None) -> Any:
    return IntelligentInvestorScreenerExample().main(argv)


if __name__ == "__main__":
    sys.exit(main())
