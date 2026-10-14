"""screening/03_dividend_yield_screener.py: High Dividend Yield Screener at 52-Week Low.

Point-in-time "Dogs of the NSE" screener evaluated strictly as of ``--asof``: high dividend
yielders trading near their 52-week low with strong capital efficiency. Emits a pure symbol/JSON
stream on ``stdout`` and diagnostics on ``stderr`` for Unix pipe chaining.

Usage:
    # High yielders at 52-week low, then pipe the symbols into the forward SIP backtest
    python screening/03_dividend_yield_screener.py --asof 2023-01-01 --min-div-yield 3.0 --max-pct-from-52w-low 10.0 --format symbols \\
        | python backtesting/10_chained_screener_and_sip.py --asof 2023-01-01 --sip-amount 10000

Pitfalls::

    - Yield is last year's cash paid over today's price: dividend per share comes from the latest
      FY cash-flow line (Cash Dividends Paid, else Common Stock Dividend Paid) divided by diluted
      average shares and the as-of close. A one-off special dividend inflates it, and a price
      collapse inflates it too, so a high yield at a 52-week low is often a coming cut, not a gift.
    - Period end, not publication date: fundamentals use fiscal_date <= --asof with no filing date
      in the tables; --fiscal-lag-days (default 0) is a flat-day proxy. NULL ROCE and NULL
      debt/equity (or no fundamentals row) fail their gates unless --allow-missing is passed,
      and a missing dividend line counts as 0 and fails the yield test.
    - The "Touch" badge is narrow: hit_52w_low_recently is true only when a bar in the last 30
      calendar days had a low within 2 percent of the 252-session low. Many candidates sit
      above that; rely on pct_from_low for the actual distance.
    - Survivorship: payers that cut, fell and delisted are not in the catalog, so a screen dated
      years back sees only the high yielders that lived. Yield screens are most flattered by this.
    - Small samples and lookahead: with the defaults the screen returned 1 name as of 2023-06-01
      and 6 as of 2025-03-01, too few to say anything, and the list moves with --min-div-yield or
      --asof. The chained backtest buys at the --asof open though the screen read that day's close.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba_examples.screener_cli import ScreenerCLIExample, rich_console
from honba_examples.screening import screen_value_and_dividend

__all__ = ["DividendYieldScreenerExample", "main"]


class DividendYieldScreenerExample(ScreenerCLIExample):
    """High dividend yield screener near 52-week low (Dogs of the NSE)."""

    train_years: int = 3
    max_pct_from_52w_low: float = 15.0
    min_div_yield: float = 3.0
    min_roce: float = 12.0
    max_pe: float = 0.0
    max_pb: float = 0.0
    max_debt_equity: float = 1.0
    require_positive_fcf: bool = True
    fiscal_lag_days: int = 0
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
            "--max-pct-from-52w-low",
            type=float,
            default=self.max_pct_from_52w_low,
            help="Maximum %% above the 52-week low (default: 15.0)",
        )
        parser.add_argument(
            "--min-div-yield",
            type=float,
            default=self.min_div_yield,
            help="Minimum dividend yield %% (default: 3.0)",
        )
        parser.add_argument(
            "--min-roce",
            type=float,
            default=self.min_roce,
            help="Minimum ROCE %% (default: 12.0)",
        )
        parser.add_argument(
            "--max-pe",
            type=float,
            default=self.max_pe,
            help="Maximum derived P/E ratio (default: 0, disabled)",
        )
        parser.add_argument(
            "--max-pb",
            type=float,
            default=self.max_pb,
            help="Maximum derived P/B ratio (default: 0, disabled)",
        )
        parser.add_argument(
            "--max-debt-equity",
            type=float,
            default=self.max_debt_equity,
            help="Maximum debt-to-equity ratio (default: 1.0)",
        )
        parser.add_argument(
            "--require-positive-fcf",
            action=argparse.BooleanOptionalAction,
            default=self.require_positive_fcf,
            help="Require positive free cash flow (default: on)",
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

    def screen(self, catalog_dir: Path, asof: dt.date) -> list[dict[str, Any]]:
        return screen_value_and_dividend(
            catalog_dir=catalog_dir,
            asof_date=asof,
            train_years=self.train_years,
            mode="dividend",
            max_pct_from_52w_low=self.max_pct_from_52w_low,
            min_div_yield=self.min_div_yield,
            min_roce=self.min_roce,
            max_pe=self.max_pe if self.max_pe and self.max_pe > 0 else None,
            max_pb=self.max_pb if self.max_pb and self.max_pb > 0 else None,
            max_debt_equity=self.max_debt_equity,
            require_positive_fcf=self.require_positive_fcf,
            fiscal_lag_days=self.fiscal_lag_days,
            max_stale_days=self.max_stale_days,
            allow_missing=self.allow_missing,
            top=self.top,
        )

    def json_payload(self, candidates: list[dict[str, Any]], asof: dt.date) -> dict[str, Any]:
        return {
            "asof_date": asof.isoformat(),
            "strategy": "dividend",
            "train_years": self.train_years,
            "candidates": candidates,
        }

    def render_table(self, candidates: list[dict[str, Any]], asof: dt.date) -> None:
        from rich import box
        from rich.table import Table

        table = Table(
            title=f"High Dividend Yielders at 52-Week Low as of {asof} ({self.train_years}-Yr Training Data)",
            border_style="cyan",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("Symbol", style="bold cyan", no_wrap=True)
        table.add_column("Company", style="dim")
        table.add_column("Price (₹)", justify="right", no_wrap=True)
        table.add_column("52w Low (₹)", justify="right", style="dim", no_wrap=True)
        table.add_column("From 52w Low", justify="right", style="yellow", no_wrap=True)
        table.add_column(f"{self.train_years}-Yr Ret", justify="right", style="dim", no_wrap=True)
        table.add_column("Hit Low?", justify="center", style="green", no_wrap=True)
        table.add_column("Div Yield", justify="right", style="magenta", no_wrap=True)
        table.add_column("P/E", justify="right", no_wrap=True)
        table.add_column("P/B", justify="right", no_wrap=True)
        table.add_column("ROCE %", justify="right", style="green", no_wrap=True)
        table.add_column("Debt/Eq", justify="right", no_wrap=True)
        table.add_column("FCF", justify="center", no_wrap=True)

        for c in candidates:
            div_s = f"{c['div_yield_pct']:.2f}%" if c.get("div_yield_pct", 0) > 0 else "-"
            pe_s = f"{c['pe_ratio']:.1f}" if c.get("pe_ratio") is not None else "-"
            pb_s = f"{c['pb_ratio']:.2f}" if c.get("pb_ratio") is not None else "-"
            roce_s = f"{c['roce_pct']:.1f}%" if c.get("roce_pct") is not None else "-"
            de_s = f"{c['debt_to_equity']:.2f}" if c.get("debt_to_equity") is not None else "-"
            fcf = c.get("free_cash_flow")
            fcf_s = "+" if fcf is not None and fcf > 0 else ("-" if fcf is not None else "?")
            hit_s = "✓ Touch" if c.get("hit_52w_low_recently") else "Near"
            table.add_row(
                c["symbol"],
                c.get("company_name", c["symbol"]),
                f"₹{c['close']:,.2f}",
                f"₹{c['low52']:,.2f}",
                f"+{c['pct_from_low']:.1f}%",
                f"{c['train_ret_pct']:+.1f}%",
                hit_s,
                div_s,
                pe_s,
                pb_s,
                roce_s,
                de_s,
                fcf_s,
            )
        rich_console().print(table)


def main(argv: list[str] | None = None) -> Any:
    return DividendYieldScreenerExample().main(argv)


if __name__ == "__main__":
    sys.exit(main())
