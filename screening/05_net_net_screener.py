"""screening/05_net_net_screener.py: Benjamin Graham Net-Net (NCAV) Screener.

Point-in-time deep asset value screener based on Benjamin Graham's Net Current Asset Value (NCAV):
- NCAV = Current Assets - Total Liabilities
- NCAV per share = NCAV / Diluted Shares
- Price-to-NCAV = Close / NCAV per share
- Margin of Safety to NCAV = (NCAV per share - Close) / NCAV per share * 100
- Classic Graham 2/3 Net-Net: Price <= 0.67 * NCAV per share (MoS >= 33.3%)
- Net Cash Bargain: Current Assets and liquid cash provide an asymmetric asset floor

Emits a pure symbol/JSON stream on ``stdout`` and diagnostics on ``stderr`` for Unix pipe chaining.

Usage:
    # Classic Graham net-nets (price <= 2/3 of NCAV per share, the default) as of 2024-01-01
    python screening/05_net_net_screener.py --asof 2024-01-01 --top 5

    # Looser: price <= 1.0 x NCAV per share
    python screening/05_net_net_screener.py --asof 2024-01-01 --max-p-ncav 1.0 --top 5

    # Rank only: no price cap, cheapest P/NCAV first (NOT a net-net screen)
    python screening/05_net_net_screener.py --asof 2024-01-01 --max-p-ncav 0 --top 5

    # Pipe symbols into the forward SIP backtester
    python screening/05_net_net_screener.py --asof 2024-01-01 --format symbols \
        | python backtesting/10_chained_screener_and_sip.py --asof 2024-01-01 --sip-amount 10000

Pitfalls::

    - Book value taken at face: NCAV is Current Assets minus Total Liabilities with receivables
      and inventory counted at 100 percent. Graham haircut them, and a company can show a
      positive NCAV on stale inventory or doubtful receivables that would not liquidate at book.
    - Often an empty result: --max-p-ncav defaults to 0.67 (Graham's two-thirds rule), which very
      few listed stocks meet; an empty list is the honest answer. Before this default was set the
      script ranked every positive-NCAV stock and returned names at roughly 4x to 13x NCAV.
      --max-p-ncav 0 restores that rank-only mode, which is not a net-net screen.
    - Stale balance sheet: the latest annual statement with fiscal_date <= --asof is used with no
      filing date in the tables, so it is up to a year old plus the reporting lag, and
      --fiscal-lag-days (default 0) is only a flat-day proxy. Shares are diluted average shares
      for the year, not the share count today.
    - Liquidity and survivorship: nothing here tests turnover or volume, and net-nets are
      typically illiquid small caps where the open or close print may not be tradeable in size.
      Companies that failed or delisted are missing from the catalog, flattering any past date.
    - Minimum history is only 100 bars, so the 52-week low and high can span a shorter window.
      The chained backtest buys at the --asof open after the screen read that day's close.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba_examples.screener_cli import ScreenerCLIExample, rich_console
from honba_examples.screening import screen_net_net

__all__ = ["NetNetScreenerExample", "main"]


class NetNetScreenerExample(ScreenerCLIExample):
    """Benjamin Graham Net-Net (Net Current Asset Value / NCAV) deep value screener."""

    train_years: int = 3
    max_p_ncav: float | None = 0.67
    min_ncav_margin_of_safety: float | None = None
    require_positive_cfo: bool = False
    require_positive_eps: bool = False
    max_debt_equity: float = 1.0
    min_current_ratio: float = 1.0
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
            "--max-p-ncav",
            type=float,
            default=self.max_p_ncav,
            help="Maximum Price-to-NCAV ratio (default: 0.67, Graham's 2/3 rule; 1.0 for plain NCAV; 0 = no cap, rank only)",
        )
        parser.add_argument(
            "--min-ncav-margin-of-safety",
            type=float,
            default=self.min_ncav_margin_of_safety,
            help="Minimum Margin of Safety %% relative to NCAV (default: disabled)",
        )
        parser.add_argument(
            "--require-positive-cfo",
            action=argparse.BooleanOptionalAction,
            default=self.require_positive_cfo,
            help="Require positive operating cash flow to protect against cash burn (default: off)",
        )
        parser.add_argument(
            "--require-positive-eps",
            action=argparse.BooleanOptionalAction,
            default=self.require_positive_eps,
            help="Require positive earnings per share (default: off)",
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
            help="Minimum current ratio (default: 1.0)",
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
        return screen_net_net(
            catalog_dir=catalog_dir,
            asof_date=asof,
            train_years=self.train_years,
            max_price_to_ncav=self.max_p_ncav if self.max_p_ncav and self.max_p_ncav > 0 else None,
            min_ncav_margin_of_safety=self.min_ncav_margin_of_safety,
            require_positive_ncav=True,
            require_positive_cfo=self.require_positive_cfo,
            require_positive_eps=self.require_positive_eps,
            max_debt_equity=self.max_debt_equity
            if self.max_debt_equity and self.max_debt_equity > 0
            else None,
            min_current_ratio=self.min_current_ratio,
            fiscal_lag_days=self.fiscal_lag_days,
            max_stale_days=self.max_stale_days,
            top=self.top,
        )

    def json_payload(self, candidates: list[dict[str, Any]], asof: dt.date) -> dict[str, Any]:
        return {
            "asof_date": asof.isoformat(),
            "strategy": "net_net",
            "train_years": self.train_years,
            "candidates": candidates,
        }

    def render_table(self, candidates: list[dict[str, Any]], asof: dt.date) -> None:
        from rich import box
        from rich.table import Table

        title = f"Stage 1 (In-Sample): Benjamin Graham Net-Net / Deep Asset Value Candidates as of {asof}"
        table = Table(title=title, border_style="cyan", box=box.ROUNDED, header_style="bold cyan")
        table.add_column("Symbol", style="bold cyan", no_wrap=True)
        table.add_column("Company", style="dim")
        table.add_column("Price (₹)", justify="right", no_wrap=True)
        table.add_column("Cur Assets", justify="right", style="dim", no_wrap=True)
        table.add_column("Tot Liab", justify="right", style="dim", no_wrap=True)
        table.add_column("NCAV/Sh (₹)", justify="right", style="bold yellow", no_wrap=True)
        table.add_column("P/NCAV", justify="right", style="bold green", no_wrap=True)
        table.add_column("NCAV MoS", justify="right", no_wrap=True)
        table.add_column("Cur Ratio", justify="right", no_wrap=True)
        table.add_column("Cash/Sh (₹)", justify="right", style="magenta", no_wrap=True)
        table.add_column("Debt/Eq", justify="right", no_wrap=True)
        table.add_column("CFO (₹Cr)", justify="right", style="green", no_wrap=True)

        for c in candidates:
            cr_s = f"{c['current_ratio']:.2f}" if c.get("current_ratio") is not None else "-"
            de_s = f"{c['debt_to_equity']:.2f}" if c.get("debt_to_equity") is not None else "-"
            p_ncav_s = f"{c['price_to_ncav']:.2f}x" if c.get("price_to_ncav") is not None else "-"
            mos_val = c.get("ncav_margin_of_safety_pct", 0)
            mos_style = "bold green" if mos_val >= 0 else "dim"
            mos_s = f"[{mos_style}]{mos_val:+.1f}%[/{mos_style}]"

            table.add_row(
                c["symbol"],
                c.get("company_name", c["symbol"]),
                f"₹{c['close']:,.2f}",
                f"₹{c['cur_assets_cr']:,.0f}Cr",
                f"₹{c['tot_liab_cr']:,.0f}Cr",
                f"₹{c['ncav_per_share']:,.2f}",
                p_ncav_s,
                mos_s,
                cr_s,
                f"₹{c['cash_per_share']:,.2f}",
                de_s,
                f"₹{c['cfo_cr']:,.0f}Cr",
            )
        rich_console().print(table)


def main(argv: list[str] | None = None) -> Any:
    return NetNetScreenerExample().main(argv)


if __name__ == "__main__":
    sys.exit(main())
