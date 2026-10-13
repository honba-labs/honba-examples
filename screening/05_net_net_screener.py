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
    # Screen deepest asset value / Net-Net candidates as of 2024-01-01
    python screening/05_net_net_screener.py --asof 2024-01-01 --top 5

    # Screen strict Net-Nets (P/NCAV <= 1.0)
    python screening/05_net_net_screener.py --asof 2024-01-01 --max-p-ncav 1.0 --top 5

    # Pipe symbols into the forward SIP backtester
    python screening/05_net_net_screener.py --asof 2024-01-01 --format symbols \
        | python backtesting/10_chained_screener_and_sip.py --asof 2024-01-01 --sip-amount 10000
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba_examples.screener_cli import ScreenerCLIExample, rich_console
from honba_examples.screening import screen_net_net

__all__ = ["NetNetScreenerExample", "main"]


class NetNetScreenerExample(ScreenerCLIExample):
    """Benjamin Graham Net-Net (Net Current Asset Value / NCAV) deep value screener."""

    train_years: int = 3
    max_p_ncav: float | None = None
    min_ncav_margin_of_safety: float | None = None
    require_positive_cfo: bool = False
    require_positive_eps: bool = False
    max_debt_equity: float = 1.0
    min_current_ratio: float = 1.0
    fiscal_lag_days: int = 0
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
            help="Maximum Price-to-NCAV ratio (e.g. 0.67 for 2/3 Net-Net, 1.0 for NCAV, default: disabled)",
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
