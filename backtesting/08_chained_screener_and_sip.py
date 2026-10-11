"""backtesting/08_chained_screener_and_sip.py: Chained Screener and Forward SIP Backtest.

Demonstrates point-in-time strategy screening with in-sample training data and
subsequent out-of-sample forward backtesting:
    [Stage 1: Prospective Screener (2-5 Years In-Sample Base & Sweet-Spot Momentum)]
                                      │
                               (top candidates)
                                      ▼
    [Stage 2: Out-of-Sample Forward Backtest for Multibagger Confirmation]
       (Initial Capital on Asof Date + Recurring Monthly SIP Until Till Date)

Inherits from:
- `honba.strategies.base.Strategy` (Honba Strategy ABC) for the systematic execution model.
- `honba_examples.base.HonbaExample` (Honba Example ABC) for standardized CLI options
  (`--capital` / `--initial-capital`, `--start` / `--asof`, `--end` / `--end-date`, `--format`).

Stage 1 (In-Sample Screening):
    Evaluates historical bars strictly on or before `--asof <YYYY-MM-DD>` (up to `--train-years` back):
    - `multibagger`: Prospective "going-to-be" multibaggers in early Stage 2 breakout sweet spot
      (1y return 15-95%, positive 3m momentum ignition, within 15% of 52w high, volume accumulation, ROCE).
      Crucially avoids already-exhausted parabolic runners (e.g. 300-500% late-stage tops).
    - `dividend`: High dividend yielders (yield >= 2.5%, FCF > 0) near 52-week low.
    - `value`: Deep value (low P/E & P/B, ROCE >= 10%, Debt/Eq <= 1.5) near 52-week low.

Stage 2 (Out-of-Sample Forward Backtest & Confirmation):
    Simulates forward systematic investment strictly from `--asof` to till date:
    - Initial capital (`--capital`) deployed across candidates on the `asof` date.
    - Monthly SIP (`--sip`) deployed every subsequent month until the terminal date.
    Calculates terminal valuation, net profit, return multiple, and exact annualized XIRR.

Usage::

    # 1. Screen prospective multibaggers as of 2026-05-10 with 2-yr base and backtest to till date:
    python backtesting/08_chained_screener_and_sip.py --strategy multibagger --train-years 2 --asof 2026-05-10 --top 5 --capital 100000 --sip 10000

    # 2. Screen prospective multibaggers as of 2023-01-01 and verify forward out-of-sample confirmation:
    python backtesting/08_chained_screener_and_sip.py --strategy multibagger --asof 2023-01-01 --train-years 3 --top 5

    # 3. Screen prospective candidates as of today without backtesting:
    python backtesting/08_chained_screener_and_sip.py --strategy multibagger --screen-only --top 5
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Ensure honba-examples root is on path
try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba.display import OutputFormat
from honba.strategies.base import Parameter, Strategy
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from honba_examples.base import HonbaExample
from honba_examples.screening import (
    find_catalog_path,
    get_latest_market_date,
    screen_multibaggers,
    screen_value_and_dividend,
)
from honba_examples.sip import simulate_portfolio_sip

__all__ = ["ChainedScreenerSipExample", "SipPortfolioStrategy", "main"]

logger = logging.getLogger(__name__)
console = Console()
err_console = Console(stderr=True)


class SipPortfolioStrategy(Strategy):
    """Systematic Investment Plan (SIP) strategy implementing Honba's Strategy ABC.

    Models monthly capital allocation and share accumulation on bar events.
    """

    name = "sip_portfolio_strategy"
    warmup_bars = 0

    monthly_amount = Parameter(
        default=10000.0,
        min=0.0,
        max=1000000.0,
        step=1000.0,
        description="Monthly SIP allocation amount in INR per stock",
    )
    initial_capital = Parameter(
        default=0.0,
        min=0.0,
        max=100000000.0,
        step=10000.0,
        description="Total initial capital budget in INR",
    )

    def __init__(self, monthly_amount: float = 10000.0, initial_capital: float = 0.0) -> None:
        super().__init__()
        self._monthly_amount = monthly_amount
        self._initial_capital = initial_capital
        self._last_month: int | None = None

    def on_bar(self, bar: Any) -> None:
        bar_date = getattr(bar, "date", None)
        if bar_date is None:
            return
        if self._last_month != bar_date.month and self._monthly_amount > 0:
            self._last_month = bar_date.month
            self.buy(bar.instrument_id, 1)


class ChainedScreenerSipExample(HonbaExample):
    """Chained In-Sample Screener and Out-of-Sample Forward SIP Backtest.

    Inherits from HonbaExample to provide standardized CLI configuration:
    - `--capital` / `--initial-capital`: Initial capital deployed on asof date
    - `--universe`, `--exchange`, `--timeframe`
    - `--start` / `--asof`: In-sample screening evaluation date
    - `--end` / `--end-date`: Out-of-sample forward backtest terminal date
    - `--data-dir`, `--out-dir`, `--format`
    """

    universe_name: str = "nifty50"
    exchange: str = "NSE"
    timeframe: str = "1D"
    start_date: dt.date = dt.date(2023, 1, 1)
    end_date: dt.date = dt.date(2026, 12, 31)
    initial_capital: float | None = None
    warmup_days: int = 0
    date_range_args: bool = True

    # Custom attributes defined as class attributes so HonbaExample.parse_args sets them
    strategy: str = "multibagger"
    asof_str: str | None = None
    end_date_str: str | None = None
    train_years: int = 3
    sip_amount: float | None = None
    sip_mode: str = "per-stock"
    top: int = 5
    screen_only: bool = False
    symbols: str | None = None
    out: Path | None = None

    def add_custom_args(self, parser: argparse.ArgumentParser) -> None:
        """Add screener and SIP specific options alongside HonbaExample base args."""
        parser.add_argument(
            "--strategy",
            choices=["multibagger", "dividend", "value", "all"],
            default=self.strategy,
            help="Screener strategy: 'multibagger' (prospective momentum), 'dividend', or 'value'",
        )
        parser.add_argument(
            "--asof",
            dest="asof_str",
            type=str,
            default=None,
            help="In-sample evaluation date (YYYY-MM-DD). If omitted, falls back to --start.",
        )
        parser.add_argument(
            "--end-date",
            dest="end_date_str",
            type=str,
            default=None,
            help="Terminal date for forward out-of-sample backtest (YYYY-MM-DD, alias for --end)",
        )
        parser.add_argument(
            "--train-years",
            type=int,
            default=self.train_years,
            help="Number of in-sample training years prior to asof date (default: 3)",
        )
        parser.add_argument(
            "--sip-amount",
            "--sip",
            dest="sip_amount",
            type=float,
            default=None,
            help="Monthly SIP installment in INR (default: ₹10,000 per stock)",
        )
        parser.add_argument(
            "--sip-mode",
            dest="sip_mode",
            choices=["per-stock", "portfolio"],
            default=self.sip_mode,
            help="How to interpret --sip: 'per-stock' (default) or 'portfolio' (divided equally among screened candidates)",
        )
        parser.add_argument(
            "--top",
            type=int,
            default=self.top,
            help="Limit screening candidates to top N (default: 5)",
        )
        parser.add_argument(
            "--screen-only",
            action="store_true",
            help="Run Stage 1 screening only (do not run forward out-of-sample backtest)",
        )
        parser.add_argument(
            "--symbols",
            type=str,
            default=None,
            help="Optional explicit symbols list (skips screening stage)",
        )
        parser.add_argument(
            "--out",
            type=Path,
            default=None,
            help="Optional path to write JSON output report",
        )

    def run(self) -> Any:
        """Execute Stage 1 screening and Stage 2 forward backtesting."""
        # 1. Resolve catalog path
        try:
            if self.data_dir:
                catalog_dir = find_catalog_path(self.data_dir)
            else:
                candidate = self.store.data_dir / "catalog" / self.timeframe / self.exchange
                catalog_dir = candidate if candidate.is_dir() else find_catalog_path()
        except FileNotFoundError as exc:
            err_console.print(f"[red]Error:[/red] {exc}")
            return 1

        # 2. Resolve in-sample date (asof) and terminal backtest date
        if self.asof_str:
            try:
                asof_eval_date = dt.date.fromisoformat(self.asof_str)
            except ValueError:
                err_console.print(
                    f"[red]Error:[/red] Invalid date format for --asof: '{self.asof_str}'"
                )
                return 1
        else:
            asof_eval_date = self.start_date

        latest_data_date = get_latest_market_date(catalog_dir)
        if self.end_date_str:
            try:
                end_eval_date = dt.date.fromisoformat(self.end_date_str)
            except ValueError:
                err_console.print(
                    f"[red]Error:[/red] Invalid date format for --end-date: '{self.end_date_str}'"
                )
                return 1
        elif self.end_date and self.end_date <= latest_data_date:
            end_eval_date = self.end_date
        else:
            end_eval_date = latest_data_date

        init_capital_val = float(self.initial_capital) if self.initial_capital is not None else None

        # 3. Stage 1: Screening (with in-sample training window)
        candidates: list[dict[str, Any]] = []
        if self.symbols:
            target_symbols = [s.strip().upper() for s in self.symbols.split(",") if s.strip()]
            for s in target_symbols:
                candidates.append(
                    {
                        "symbol": s,
                        "asof_date": asof_eval_date.isoformat(),
                        "close": 0.0,
                        "ret_1y": 0.0,
                        "ret_3m": 0.0,
                        "train_ret_pct": 0.0,
                        "pct_from_high": 0.0,
                        "sma200": 0.0,
                        "sma200_rising": True,
                        "vol_surge": 1.0,
                        "roce_pct": None,
                        "debt_to_equity": None,
                        "mb_score": 100.0,
                    }
                )
        else:
            if self.strategy == "multibagger":
                candidates = screen_multibaggers(
                    catalog_dir=catalog_dir,
                    asof_date=asof_eval_date,
                    train_years=self.train_years,
                    top=self.top,
                )
            else:
                candidates = screen_value_and_dividend(
                    catalog_dir=catalog_dir,
                    asof_date=asof_eval_date,
                    train_years=self.train_years,
                    mode=self.strategy,
                    top=self.top,
                )
            target_symbols = [c["symbol"] for c in candidates]

        # Handle plain symbols output
        if self.output_format == OutputFormat.PLAIN:
            print(" ".join(target_symbols))
            return 0

        if not target_symbols:
            err_console.print(
                f"[yellow]No candidates found for strategy '{self.strategy}' as of {asof_eval_date}.[/yellow]"
            )
            return 0

        # Display Stage 1 results (if not JSON)
        if self.output_format != OutputFormat.JSON:
            self._render_screener_table(candidates, asof_eval_date, self.strategy, self.train_years)

        # If --screen-only requested, stop here
        if self.screen_only:
            if self.output_format == OutputFormat.JSON:
                print(json.dumps({"stage": "screening", "candidates": candidates}, indent=2))
            return 0

        # 4. Stage 2: Out-of-Sample Forward Backtest (initial capital on asof + monthly SIP to end_date)
        portfolio_results = simulate_portfolio_sip(
            catalog_dir=catalog_dir,
            symbols=target_symbols,
            start_date=asof_eval_date,
            end_date=end_eval_date,
            initial_capital=init_capital_val,
            monthly_sip=self.sip_amount,
            sip_mode=self.sip_mode,
        )

        combined_output = {
            "asof_date": asof_eval_date.isoformat(),
            "train_years": self.train_years,
            "end_date": end_eval_date.isoformat(),
            "strategy": self.strategy,
            "initial_capital": init_capital_val,
            "sip_amount": self.sip_amount,
            "sip_mode": self.sip_mode,
            "screening_candidates": candidates,
            "portfolio_backtest": portfolio_results,
        }

        if self.out:
            self.out.parent.mkdir(parents=True, exist_ok=True)
            self.out.write_text(json.dumps(combined_output, indent=2) + "\n", encoding="utf-8")
            console.print(f"[green]Saved full report to {self.out}[/green]")

        if self.output_format == OutputFormat.JSON:
            print(json.dumps(combined_output, indent=2))
            return 0

        self._render_backtest_table(
            portfolio_results,
            asof_eval_date,
            end_eval_date,
        )
        return 0

    @staticmethod
    def _render_screener_table(
        candidates: list[dict[str, Any]], asof_date: dt.date, strategy: str, train_years: int
    ) -> None:
        """Render Rich terminal table for Stage 1 screened candidates."""
        if strategy == "multibagger":
            title = f"Stage 1 (In-Sample): Prospective 'Going-to-Be' Multibaggers as of {asof_date} ({train_years}-Yr Base Window)"
            table = Table(title=title, border_style="cyan")
            table.add_column("Symbol", style="bold cyan")
            table.add_column("Price (₹)", justify="right")
            table.add_column("1-Yr Ret", justify="right", style="green")
            table.add_column("3-Mo Surge", justify="right", style="bold green")
            table.add_column("From 52w High", justify="right", style="magenta")
            table.add_column("Vol Surge", justify="right", style="yellow")
            table.add_column("200 SMA", justify="right", style="dim")
            table.add_column("ROCE %", justify="right", style="yellow")
            table.add_column("MB Score", justify="right", style="bold cyan")

            for c in candidates:
                roce_s = f"{c['roce_pct']:.1f}%" if c.get("roce_pct") is not None else "-"
                vol_s = f"{c['vol_surge']:.2f}x" if c.get("vol_surge") is not None else "-"
                table.add_row(
                    c["symbol"],
                    f"₹{c['close']:,.2f}",
                    f"{c['ret_1y']:+.1f}%",
                    f"{c.get('ret_3m', 0.0):+.1f}%",
                    f"{c['pct_from_high']:+.1f}%",
                    vol_s,
                    f"₹{c['sma200']:,.2f}",
                    roce_s,
                    f"{c.get('mb_score', 0.0):.1f}",
                )
            console.print(table)
        else:
            title = f"Stage 1 (In-Sample): Screened {strategy.title()} Candidates as of {asof_date} ({train_years}-Yr Training Data)"
            table = Table(title=title, border_style="cyan")
            table.add_column("Symbol", style="bold cyan")
            table.add_column("Price (₹)", justify="right")
            table.add_column("52w Low (₹)", justify="right", style="dim")
            table.add_column("From 52w Low", justify="right", style="yellow")
            table.add_column(f"{train_years}-Yr Train Ret", justify="right", style="dim")
            table.add_column("Hit Low?", justify="center", style="bold green")
            table.add_column("Div Yield %", justify="right", style="bold magenta")
            table.add_column("P/E", justify="right")
            table.add_column("ROCE %", justify="right", style="green")
            table.add_column("Debt/Eq", justify="right")

            for c in candidates:
                div_s = f"{c['div_yield_pct']:.2f}%" if c.get("div_yield_pct", 0) > 0 else "-"
                pe_s = f"{c['pe_ratio']:.1f}" if c.get("pe_ratio") is not None else "-"
                roce_s = f"{c['roce_pct']:.1f}%" if c.get("roce_pct") is not None else "-"
                de_s = f"{c['debt_to_equity']:.2f}" if c.get("debt_to_equity") is not None else "-"
                hit_s = "✓ Touch" if c.get("hit_52w_low_recently") else "Near"
                table.add_row(
                    c["symbol"],
                    f"₹{c['close']:,.2f}",
                    f"₹{c['low52']:,.2f}",
                    f"+{c['pct_from_low']:.1f}%",
                    f"{c['train_ret_pct']:+.1f}%",
                    hit_s,
                    div_s,
                    pe_s,
                    roce_s,
                    de_s,
                )
            console.print(table)

    @staticmethod
    def _render_backtest_table(
        portfolio: dict[str, Any],
        asof_date: dt.date,
        end_date: dt.date,
    ) -> None:
        """Render Rich terminal table and summary panel for Stage 2 out-of-sample forward backtest."""
        stocks: list[dict[str, Any]] = portfolio["stocks"]
        summary: dict[str, Any] = portfolio["summary"]
        init_capital = summary.get("initial_capital", 0.0)
        sip_amount = summary.get("monthly_sip", 0.0)
        window_months = summary.get("window_months", 1)

        console.print()
        if init_capital > 0 and sip_amount > 0:
            header = f"Stage 2 (Out-of-Sample Forward Confirmation): Initial Capital ₹{init_capital:,.0f} + Monthly SIP ₹{sip_amount:,.0f}/mo ({asof_date} to {end_date})"
        elif init_capital > 0:
            header = f"Stage 2 (Out-of-Sample Forward Confirmation): Initial Capital ₹{init_capital:,.0f} Lump Sum ({asof_date} to {end_date})"
        else:
            header = f"Stage 2 (Out-of-Sample Forward Confirmation): Monthly SIP ₹{sip_amount:,.0f}/mo ({asof_date} to {end_date})"

        table = Table(title=header, border_style="green")
        table.add_column("Symbol", style="bold cyan")
        table.add_column("Months", justify="center")
        if init_capital > 0:
            table.add_column("Init Cap", justify="right", style="cyan")
        if sip_amount > 0:
            table.add_column("SIP / Mo", justify="right", style="dim")
        table.add_column("Total Invested", justify="right")
        table.add_column("Final Valuation", justify="right", style="bold green")
        table.add_column("Net Profit (₹)", justify="right")
        table.add_column("SIP Mult", justify="right", style="bold yellow")
        table.add_column("Lump Mult", justify="right", style="bold green")
        table.add_column("Peak Mult", justify="right", style="magenta")
        table.add_column("XIRR (p.a.)", justify="right", style="bold magenta")
        table.add_column("Confirmation Status", justify="center", style="bold cyan")

        confirmed_count = 0
        for r in stocks:
            profit_style = "green" if r["profit"] >= 0 else "red"
            xirr_str = f"{r['xirr_pct']:+.1f}%" if r["xirr_pct"] is not None else "N/A"
            conf = r.get("confirmation", "N/A")
            if "Multibagger" in conf:
                confirmed_count += 1
                conf_style = "bold green"
            elif "Outperformer" in conf:
                conf_style = "green"
            else:
                conf_style = "dim"

            row_data = [
                r["symbol"],
                str(r["months"]),
            ]
            if init_capital > 0:
                row_data.append(f"₹{r.get('init_capital', 0.0):,.0f}")
            if sip_amount > 0:
                row_data.append(f"₹{r['sip_amount']:,.0f}")
            row_data.extend(
                [
                    f"₹{r['total_invested']:,.0f}",
                    f"₹{r['final_val']:,.0f}",
                    f"[{profit_style}]₹{r['profit']:+,.0f}[/{profit_style}]",
                    f"{r['sip_multiple']:.2f}x",
                    f"{r['lump_multiple']:.2f}x",
                    f"{r.get('peak_multiple', r['lump_multiple']):.2f}x",
                    xirr_str,
                    f"[{conf_style}]{conf}[/{conf_style}]",
                ]
            )
            table.add_row(*row_data)

        console.print(table)

        p_xirr_str = (
            f"{summary['portfolio_xirr']:+.1f}% p.a."
            if summary["portfolio_xirr"] is not None
            else "N/A"
        )
        p_profit_pct = (
            (summary["total_profit"] / summary["total_invested"]) * 100.0
            if summary["total_invested"] > 0
            else 0.0
        )
        conf_rate = (
            f"{confirmed_count}/{len(stocks)} ({(confirmed_count / len(stocks)) * 100:.0f}%)"
            if stocks
            else "0/0"
        )

        # Build informative summary panel lines
        panel_lines = [
            f"[bold]In-Sample Screening Date :[/bold] [cyan]{asof_date}[/cyan]",
            f"[bold]Out-of-Sample Test Window:[/bold] [yellow]{asof_date}[/yellow] to [yellow]{end_date}[/yellow] ({window_months} months)",
        ]
        if init_capital > 0:
            panel_lines.append(
                f"[bold]Initial Capital Deployed :[/bold] ₹{init_capital:,.2f} (invested on {asof_date} across {len(stocks)} stocks)"
            )
        if sip_amount > 0:
            panel_lines.append(
                f"[bold]Monthly SIP Installment  :[/bold] ₹{sip_amount:,.2f}/month per stock (Total SIP: ₹{summary['total_sip_invested']:,.2f} over {window_months} months)"
            )
        panel_lines.extend(
            [
                f"[bold]Total Capital Deployed   :[/bold] ₹{summary['total_invested']:,.2f} (Initial Capital + Forward SIP from {asof_date})",
                f"[bold]Terminal Portfolio Value :[/bold] [bold green]₹{summary['total_value']:,.2f}[/bold green]",
                f"[bold]Total Net Profit         :[/bold] [bold green]₹{summary['total_profit']:+,.2f}[/bold green] ([green]{p_profit_pct:+.1f}%[/green])",
                f"[bold]Overall Portfolio Mult   :[/bold] [bold yellow]{summary['portfolio_multiple']:.2f}x[/bold yellow]",
                f"[bold]Portfolio Annual XIRR    :[/bold] [bold magenta]{p_xirr_str}[/bold magenta]",
                f"[bold]Multibagger Confirmation :[/bold] [bold green]{conf_rate} confirmed as >=2x/3x multibaggers![/bold green]",
            ]
        )

        console.print()
        console.print(
            Panel(
                "\n".join(panel_lines),
                title="In-Sample Hypothesis vs Out-of-Sample SIP Confirmation",
                border_style="magenta",
            )
        )


def main(argv: list[str] | None = None) -> Any:
    return ChainedScreenerSipExample().main(argv)


if __name__ == "__main__":
    sys.exit(main())
