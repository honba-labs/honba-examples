"""backtesting/08_chained_screener_and_sip.py: Chained Screener and Forward SIP Backtest.

Demonstrates point-in-time strategy screening with in-sample training data and
subsequent out-of-sample forward backtesting:
    [Stage 1: Screener (3-5 Years In-Sample Training Data)]
                             │
                      (top candidates)
                             ▼
    [Stage 2: Out-of-Sample Forward Monthly SIP Backtest (As-Of to Till Date)]

Stage 1 (In-Sample Screening):
    Evaluates up to 3-5 years of historical bars strictly on or before `--asof <YYYY-MM-DD>`:
    - `multibagger`: Stage 2 momentum (1y return > 35%, price > 200 SMA rising, near 52w high).
    - `dividend`: High dividend yielders (yield >= 2.5%, FCF > 0) near 52-week low.
    - `value`: Deep value (low P/E & P/B, ROCE >= 10%, Debt/Eq <= 1.5) near 52-week low.

Stage 2 (Out-of-Sample Forward Backtest):
    Simulates forward monthly systematic investment (SIP, default: ₹10,000/month each)
    from `--asof` to the present (or `--end-date`), calculating accumulated units,
    terminal valuation, net profit, return multiple, and exact annualized XIRR.

Usage::

    # 1. Screen multibaggers with 3-yr training data as of 2023-01-01 and backtest forward SIP:
    python backtesting/08_chained_screener_and_sip.py --strategy multibagger --asof 2023-01-01 --train-years 3 --top 5

    # 2. Screen high dividend yielders at 52-week low as of 2023-01-01 and backtest forward SIP:
    python backtesting/08_chained_screener_and_sip.py --strategy dividend --asof 2023-01-01 --top 5

    # 3. Find candidates as of today without backtesting:
    python backtesting/08_chained_screener_and_sip.py --strategy multibagger --screen-only --top 5
    python backtesting/08_chained_screener_and_sip.py --strategy value --screen-only --top 5

    # 4. Custom list of symbols with forward SIP backtest:
    python backtesting/08_chained_screener_and_sip.py --symbols TVSMOTOR,MAZDOCK,CGPOWER --asof 2023-01-01
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

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from honba_examples.screening import (
    find_catalog_path,
    get_latest_market_date,
    screen_multibaggers,
    screen_value_and_dividend,
)
from honba_examples.sip import simulate_portfolio_sip

logger = logging.getLogger(__name__)
console = Console()
err_console = Console(stderr=True)


def render_screener_table(
    candidates: list[dict[str, Any]], asof_date: dt.date, strategy: str, train_years: int
) -> None:
    """Render Rich terminal table for Stage 1 screened candidates."""
    if strategy == "multibagger":
        title = f"Stage 1 (In-Sample): Screened Multibaggers as of {asof_date} ({train_years}-Yr Training Data)"
        table = Table(title=title, border_style="cyan")
        table.add_column("Symbol", style="bold cyan")
        table.add_column("Price (₹)", justify="right")
        table.add_column("1-Yr Ret", justify="right", style="green")
        table.add_column(f"{train_years}-Yr Train Ret", justify="right", style="bold green")
        table.add_column("From 52w High", justify="right", style="magenta")
        table.add_column("200 SMA", justify="right", style="dim")
        table.add_column("Trend Status", style="green")
        table.add_column("ROCE %", justify="right", style="yellow")
        table.add_column("Debt/Eq", justify="right")

        for c in candidates:
            roce_s = f"{c['roce_pct']:.1f}%" if c.get("roce_pct") is not None else "-"
            de_s = f"{c['debt_to_equity']:.2f}" if c.get("debt_to_equity") is not None else "-"
            trend_s = "Stage 2 Strong" if c.get("sma200_rising") else "Stage 2 Base"
            table.add_row(
                c["symbol"],
                f"₹{c['close']:,.2f}",
                f"{c['ret_1y']:+.1f}%",
                f"{c['train_ret_pct']:+.1f}%",
                f"{c['pct_from_high']:+.1f}%",
                f"₹{c['sma200']:,.2f}",
                trend_s,
                roce_s,
                de_s,
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


def render_backtest_table(
    portfolio: dict[str, Any], sip_amount: float, asof_date: dt.date, end_date: dt.date
) -> None:
    """Render Rich terminal table and summary panel for Stage 2 out-of-sample forward SIP backtest."""
    stocks: list[dict[str, Any]] = portfolio["stocks"]
    summary: dict[str, Any] = portfolio["summary"]

    console.print()
    table = Table(
        title=f"Stage 2 (Out-of-Sample Forward Backtest): Monthly SIP (₹{sip_amount:,.0f}/mo from {asof_date} to {end_date})",
        border_style="green",
    )
    table.add_column("Symbol", style="bold cyan")
    table.add_column("Months", justify="center")
    table.add_column("Total Invested", justify="right")
    table.add_column("Final Valuation", justify="right", style="bold green")
    table.add_column("Net Profit (₹)", justify="right")
    table.add_column("SIP Mult", justify="right", style="bold yellow")
    table.add_column("XIRR (p.a.)", justify="right", style="bold magenta")
    table.add_column("Lump Sum Mult", justify="right", style="dim")

    for r in stocks:
        profit_style = "green" if r["profit"] >= 0 else "red"
        xirr_str = f"{r['xirr_pct']:+.1f}%" if r["xirr_pct"] is not None else "N/A"
        table.add_row(
            r["symbol"],
            str(r["months"]),
            f"₹{r['total_invested']:,.0f}",
            f"₹{r['final_val']:,.0f}",
            f"[{profit_style}]₹{r['profit']:+,.0f}[/{profit_style}]",
            f"{r['sip_multiple']:.2f}x",
            xirr_str,
            f"{r['lump_multiple']:.2f}x",
        )

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
    months_count = stocks[0]["months"] if stocks else 0
    console.print()
    console.print(
        Panel(
            f"[bold]In-Sample Screening Date :[/bold] [cyan]{asof_date}[/cyan]\n"
            f"[bold]Out-of-Sample Test Window:[/bold] [yellow]{asof_date}[/yellow] to [yellow]{end_date}[/yellow] ({months_count} months)\n"
            f"[bold]Total Capital Deployed   :[/bold] ₹{summary['total_invested']:,.2f} (across {summary['candidate_count']} stocks)\n"
            f"[bold]Terminal Portfolio Value :[/bold] [bold green]₹{summary['total_value']:,.2f}[/bold green]\n"
            f"[bold]Total Net Profit         :[/bold] [bold green]₹{summary['total_profit']:+,.2f}[/bold green] ([green]{p_profit_pct:+.1f}%[/green])\n"
            f"[bold]Overall SIP Multiple     :[/bold] [bold yellow]{summary['portfolio_multiple']:.2f}x[/bold yellow]\n"
            f"[bold]Portfolio Annual XIRR    :[/bold] [bold magenta]{p_xirr_str}[/bold magenta]",
            title="In-Sample vs Out-of-Sample Performance Comparison",
            border_style="magenta",
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Chained Point-in-Time Screener & Out-of-Sample Forward SIP Backtest"
    )
    parser.add_argument(
        "--strategy",
        choices=["multibagger", "dividend", "value", "all"],
        default="multibagger",
        help="Screener strategy: 'multibagger' (momentum), 'dividend' (yield at 52w low), or 'value' (low P/E at 52w low)",
    )
    parser.add_argument(
        "--asof",
        type=str,
        default=None,
        help="In-sample evaluation date (YYYY-MM-DD). Defaults to latest market session.",
    )
    parser.add_argument(
        "--train-years",
        type=int,
        default=3,
        help="Number of in-sample training years prior to asof date (default: 3)",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="Terminal date for forward out-of-sample backtest (default: latest data session)",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Optional explicit symbols list (skips screening stage)",
    )
    parser.add_argument(
        "--sip-amount",
        type=float,
        default=10000.0,
        help="Monthly investment per stock in ₹ (default: 10000.0)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help="Limit screening candidates to top N (default: 5)",
    )
    parser.add_argument(
        "--screen-only",
        action="store_true",
        help="Run Stage 1 screening only (do not run forward out-of-sample backtest)",
    )
    parser.add_argument(
        "--catalog-dir",
        type=Path,
        default=None,
        help="Path to 1D NSE catalog directory",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json", "symbols"],
        default="table",
        help="Output format: table (default), json, or symbols (plain list)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional path to write JSON output report",
    )

    args = parser.parse_args(argv)

    try:
        catalog_dir = find_catalog_path(args.catalog_dir)
    except FileNotFoundError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        return 1

    if args.asof:
        try:
            asof_date = dt.date.fromisoformat(args.asof)
        except ValueError:
            err_console.print(f"[red]Error:[/red] Invalid date format for --asof: '{args.asof}'")
            return 1
    else:
        asof_date = get_latest_market_date(catalog_dir)

    if args.end_date:
        try:
            end_date = dt.date.fromisoformat(args.end_date)
        except ValueError:
            err_console.print(
                f"[red]Error:[/red] Invalid date format for --end-date: '{args.end_date}'"
            )
            return 1
    else:
        end_date = get_latest_market_date(catalog_dir)

    # =========================================================================
    # STAGE 1: Screening (with in-sample training window)
    # =========================================================================
    candidates: list[dict[str, Any]] = []
    if args.symbols:
        target_symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        for s in target_symbols:
            candidates.append(
                {
                    "symbol": s,
                    "asof_date": asof_date.isoformat(),
                    "close": 0.0,
                    "ret_1y": 0.0,
                    "train_ret_pct": 0.0,
                    "pct_from_high": 0.0,
                    "sma200": 0.0,
                    "sma200_rising": True,
                    "roce_pct": None,
                    "debt_to_equity": None,
                }
            )
    else:
        if args.strategy == "multibagger":
            candidates = screen_multibaggers(
                catalog_dir=catalog_dir,
                asof_date=asof_date,
                train_years=args.train_years,
                top=args.top,
            )
        else:
            candidates = screen_value_and_dividend(
                catalog_dir=catalog_dir,
                asof_date=asof_date,
                train_years=args.train_years,
                mode=args.strategy,
                top=args.top,
            )
        target_symbols = [c["symbol"] for c in candidates]

    # Handle --format symbols for Stage 1
    if args.format == "symbols":
        print(" ".join(target_symbols))
        return 0

    if not target_symbols:
        err_console.print(
            f"[yellow]No candidates found for strategy '{args.strategy}' as of {asof_date}.[/yellow]"
        )
        return 0

    # Display Stage 1 results (if not raw json)
    if args.format != "json":
        render_screener_table(candidates, asof_date, args.strategy, args.train_years)

    # If --screen-only was requested, stop here
    if args.screen_only:
        if args.format == "json":
            print(json.dumps({"stage": "screening", "candidates": candidates}, indent=2))
        return 0

    # =========================================================================
    # STAGE 2: Chained Out-of-Sample Forward SIP Backtest
    # =========================================================================
    portfolio_results = simulate_portfolio_sip(
        catalog_dir=catalog_dir,
        symbols=target_symbols,
        start_date=asof_date,
        end_date=end_date,
        monthly_amt=args.sip_amount,
    )

    combined_output = {
        "asof_date": asof_date.isoformat(),
        "train_years": args.train_years,
        "end_date": end_date.isoformat(),
        "strategy": args.strategy,
        "screening_candidates": candidates,
        "portfolio_backtest": portfolio_results,
    }

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(combined_output, indent=2) + "\n", encoding="utf-8")
        console.print(f"[green]Saved full report to {args.out}[/green]")

    if args.format == "json":
        print(json.dumps(combined_output, indent=2))
        return 0

    render_backtest_table(portfolio_results, args.sip_amount, asof_date, end_date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
