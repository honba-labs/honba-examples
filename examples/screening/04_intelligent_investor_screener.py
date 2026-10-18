"""screening/04_intelligent_investor_screener.py: Benjamin Graham Value Screener.

Point-in-time value screener based on Benjamin Graham's *The Intelligent Investor*:
- Graham Number = sqrt(22.5 * EPS * BVPS)
- Margin of Safety = (Graham Number - Price) / Graham Number * 100
- Valuation discipline: P/E <= 15-20, P/B <= 1.5-2.0, P/E * P/B <= 22.5
- Financial condition: Low Debt-to-Equity, robust Current Ratio, positive earnings and dividends

Emits a pure symbol/JSON stream on ``stdout`` and diagnostics on ``stderr`` for Unix pipe chaining.

Usage:
    # Screen top 5 Graham defensive value candidates as of 2024-01-01
    python examples/screening/04_intelligent_investor_screener.py --asof 2024-01-01 --mode defensive --top 5

    # Pipe symbols into the forward SIP backtester
    python examples/screening/04_intelligent_investor_screener.py --asof 2024-01-01 --format symbols \\
        | python examples/backtesting/10_chained_screener_and_sip.py --asof 2024-01-01 --sip-amount 10000

Pitfalls::

    - A loose adaptation of Graham, not his tests: EPS and book value per share come from the
      single latest FY row, where Graham averaged earnings over several years and required
      long earnings and dividend records. One good or one bad year moves the Graham Number a lot.
    - Per-share figures use diluted average shares for the year, not shares at the balance-sheet
      date, so buybacks and issues distort book value per share, P/B and the margin of safety.
      Financials and capital-intensive firms are not treated separately: debt/equity and current
      ratio mean little for banks.
    - Mode sets the default thresholds: --mode defensive uses P/E 15, P/B 1.5, P/E x P/B 22.5 and
      debt/equity 0.6, --mode enterprising uses P/E 20, P/B 2 and debt/equity 1.0. An explicit
      --max-pe, --max-pb, --max-graham-product or --max-debt-equity overrides the mode value (0
      turns that cap off) and the effective thresholds are printed to stderr. Defensive also
      still requires a dividend yield of at least 0.5 percent whatever flags you pass. A missing
      debt/equity, current assets or current liabilities fails its test unless --allow-missing.
    - Period end plus a flat lag, not publication date: fundamentals use fiscal_date <= --asof
      minus --fiscal-lag-days (default 90); no filing-date column exists, so this is a proxy. EPS
      and share count are read from the same fiscal date, and a symbol whose ratios and
      statements do not share one is dropped.
    - Survivorship and lookahead: delisted value traps are absent from the catalog, and chaining
      to the SIP backtest buys at the --asof open after reading that day's close.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

from honba_examples.screener_cli import ScreenerCLIExample, rich_console
from honba_examples.screening import graham_thresholds, screen_intelligent_investor

__all__ = ["IntelligentInvestorScreenerExample", "main"]


class IntelligentInvestorScreenerExample(ScreenerCLIExample):
    """Benjamin Graham value screener (Defensive & Enterprising investor profiles)."""

    train_years: int = 3
    mode: str = "defensive"
    min_margin_of_safety: float = 0.0
    max_graham_product: float | None = None
    max_pe: float | None = None
    max_pb: float | None = None
    max_debt_equity: float | None = None
    min_current_ratio: float = 1.2
    min_div_yield: float = 0.0
    require_positive_fcf: bool = False
    fiscal_lag_days: int = 90
    max_stale_days: int = 10
    allow_missing: bool = False
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
            help="Maximum Graham product P/E * P/B (default: mode value, 22.5; 0 disables)",
        )
        parser.add_argument(
            "--max-pe",
            type=float,
            default=self.max_pe,
            help="Maximum derived P/E (default: mode value, 15 defensive / 20 enterprising; 0 disables)",
        )
        parser.add_argument(
            "--max-pb",
            type=float,
            default=self.max_pb,
            help="Maximum derived P/B (default: mode value, 1.5 defensive / 2.0 enterprising; 0 disables)",
        )
        parser.add_argument(
            "--max-debt-equity",
            type=float,
            default=self.max_debt_equity,
            help="Maximum debt-to-equity (default: mode value, 0.6 defensive / 1.0 enterprising; 0 disables)",
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
            help=(
                "Use fundamentals whose fiscal period ended at least N days before --asof "
                "(default: 90; an approximation of the disclosure lag, not a filing date)"
            ),
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
            "--allow-missing",
            action=argparse.BooleanOptionalAction,
            default=self.allow_missing,
            help=(
                "Let symbols with missing fundamentals (NULL or no row) pass the debt/ROCE/current-"
                "ratio gates instead of failing them (default: off)"
            ),
        )
        parser.add_argument(
            "--top", type=int, default=self.top, help="Limit to top N candidates (default: 10)"
        )

    def effective_thresholds(self) -> dict[str, float | None]:
        """Caps in force: explicit flags override the ``--mode`` defaults (0 disables a cap)."""
        return graham_thresholds(
            self.mode, self.max_graham_product, self.max_pe, self.max_pb, self.max_debt_equity
        )

    def _describe_thresholds(self) -> str:
        eff = self.effective_thresholds()

        def fmt(label: str, key: str) -> str:
            value = eff[key]
            return f"{label} <= {value:g}" if value is not None else f"{label} off"

        return (
            f"Graham '{self.mode}' thresholds: {fmt('P/E', 'max_pe')}, {fmt('P/B', 'max_pb')}, "
            f"{fmt('P/E x P/B', 'max_graham_product')}, {fmt('D/E', 'max_debt_equity')}, "
            f"current ratio >= {self.min_current_ratio:g}"
        )

    def screen(self, catalog_dir: Path, asof: dt.date) -> list[dict[str, Any]]:
        print(self._describe_thresholds(), file=sys.stderr)
        return screen_intelligent_investor(
            catalog_dir=catalog_dir,
            asof_date=asof,
            train_years=self.train_years,
            mode=self.mode,
            min_margin_of_safety=self.min_margin_of_safety,
            max_graham_product=self.max_graham_product,
            max_pe=self.max_pe,
            max_pb=self.max_pb,
            max_debt_equity=self.max_debt_equity,
            min_current_ratio=self.min_current_ratio,
            min_div_yield=self.min_div_yield,
            require_positive_fcf=self.require_positive_fcf,
            fiscal_lag_days=self.fiscal_lag_days,
            max_stale_days=self.max_stale_days,
            allow_missing=self.allow_missing,
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

        product_cap = self.effective_thresholds()["max_graham_product"]
        cap_s = f"{product_cap:.1f}" if product_cap is not None else "off"
        title = (
            f"Stage 1 (In-Sample): Benjamin Graham '{self.mode.title()}' Value Candidates "
            f"as of {asof} (P/E×P/B ≤ {cap_s})"
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
