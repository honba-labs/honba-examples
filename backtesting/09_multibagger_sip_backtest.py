"""backtesting/09_multibagger_sip_backtest.py: Forward SIP Backtest on Screened Multibaggers.

Simulates a recurring systematic monthly investment (SIP, default: ₹10,000/month each)
on candidates identified by the multibagger screener (or custom symbols list)
from `--asof` to the present (or `--end-date`).

Evaluates out-of-sample forward compounding:
- Total capital invested
- Shares accumulated & volume-weighted average price
- Terminal market valuation & net PnL
- SIP return multiple and exact annualized XIRR
- Portfolio aggregate performance metrics

Usage::

    # Backtest screened candidates from 2023-01-01 to present:
    python backtesting/09_multibagger_sip_backtest.py --asof 2023-01-01

    # Backtest with custom monthly SIP amount and limit to top 5:
    python backtesting/09_multibagger_sip_backtest.py --asof 2023-01-01 --sip-amount 15000 --top 5

    # Backtest custom list of symbols:
    python backtesting/09_multibagger_sip_backtest.py --symbols SOLARINDS,TVSMOTOR,CUMMINSIND --asof 2023-01-01
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Ensure honba-examples root is on path
try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pyarrow as pa
import pyarrow.parquet as pq
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Dynamically load screener functions from 08_multibagger_screener.py
_screener_file = Path(__file__).parent / "08_multibagger_screener.py"
_spec = importlib.util.spec_from_file_location("multibagger_screener_mod", _screener_file)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load screener module from {_screener_file}")
_screener_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_screener_mod)

find_catalog_path: Callable[..., Path] = _screener_mod.find_catalog_path
get_latest_market_date: Callable[..., dt.date] = _screener_mod.get_latest_market_date
screen_multibaggers: Callable[..., list[dict[str, Any]]] = _screener_mod.screen_multibaggers

logger = logging.getLogger(__name__)
console = Console()
err_console = Console(stderr=True)


def calculate_xirr(cash_flows: list[tuple[dt.date, float]]) -> float | None:
    """Calculate exact annualized Internal Rate of Return (XIRR)."""
    if len(cash_flows) < 2:
        return None
    amounts = [a for _, a in cash_flows]
    if not (any(a < 0 for a in amounts) and any(a > 0 for a in amounts)):
        return None

    d0 = cash_flows[0][0]
    years = [(d - d0).days / 365.25 for d, _ in cash_flows]

    def npv(rate: float) -> float:
        return sum(cf / ((1.0 + rate) ** y) for cf, y in zip(amounts, years))

    # Bisection search between -99% and +1000%
    low, high = -0.99, 10.0
    mid = 0.0
    for _ in range(80):
        mid = (low + high) / 2.0
        val = npv(mid)
        if abs(val) < 1e-4:
            break
        if val > 0:
            low = mid
        else:
            high = mid
    return mid * 100.0


def load_forward_bars(
    catalog_dir: Path, symbol: str, start_date: dt.date, end_date: dt.date
) -> list[dict[str, Any]]:
    """Load daily bars for a symbol strictly between start_date and end_date."""
    sym_dir = catalog_dir / symbol
    if not sym_dir.is_dir():
        return []

    parquet_files = sorted(sym_dir.glob("*.parquet"))
    raw_bars: list[dict[str, Any]] = []

    for f in parquet_files:
        try:
            tbl = pq.read_table(f)
            ts_list: list[int] = tbl.column("ts").to_pylist()
            open_list: list[float] = [float(x) for x in tbl.column("open").to_pylist()]
            close_list: list[float] = [float(x) for x in tbl.column("close").to_pylist()]

            for t, o, c in zip(ts_list, open_list, close_list):
                d = dt.datetime.fromtimestamp(t / 1e9, dt.timezone.utc).date()
                if start_date <= d <= end_date:
                    raw_bars.append({"date": d, "open": o, "close": c})
        except (pa.ArrowException, OSError) as exc:
            logger.debug("Failed reading %s: %s", f, exc)

    raw_bars.sort(key=lambda b: b["date"])
    return raw_bars


def simulate_single_stock_sip(
    bars: list[dict[str, Any]], monthly_amt: float
) -> dict[str, Any] | None:
    """Simulate monthly SIP on a sequence of daily bars."""
    if len(bars) < 5:
        return None

    # Group by (year, month) and select the first trading day of each month
    seen_months: set[tuple[int, int]] = set()
    monthly_executions: list[dict[str, Any]] = []

    for b in bars:
        d: dt.date = b["date"]
        ym = (d.year, d.month)
        if ym not in seen_months:
            seen_months.add(ym)
            monthly_executions.append(b)

    if not monthly_executions:
        return None

    shares = 0.0
    cfs: list[tuple[dt.date, float]] = []

    for m_bar in monthly_executions:
        d = m_bar["date"]
        price = m_bar["open"]
        shares += monthly_amt / price
        cfs.append((d, -monthly_amt))

    # Terminal valuation
    last_bar = bars[-1]
    term_date: dt.date = last_bar["date"]
    term_price: float = last_bar["close"]
    final_val = shares * term_price
    cfs.append((term_date, final_val))

    total_invested = len(monthly_executions) * monthly_amt
    profit = final_val - total_invested
    sip_multiple = final_val / total_invested if total_invested > 0 else 1.0
    xirr_val = calculate_xirr(cfs)

    first_price = monthly_executions[0]["open"]
    lump_multiple = term_price / first_price if first_price > 0 else 1.0

    return {
        "months": len(monthly_executions),
        "total_invested": total_invested,
        "shares_held": shares,
        "avg_price": total_invested / shares if shares > 0 else 0.0,
        "final_price": term_price,
        "final_val": final_val,
        "profit": profit,
        "sip_multiple": sip_multiple,
        "xirr_pct": xirr_val,
        "lump_multiple": lump_multiple,
        "first_date": monthly_executions[0]["date"].isoformat(),
        "last_date": term_date.isoformat(),
        "cash_flows": cfs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Forward Monthly SIP Backtest on Multibagger Candidates"
    )
    parser.add_argument(
        "--asof",
        type=str,
        default="2023-01-01",
        help="Screening and investment starting date (YYYY-MM-DD), default: 2023-01-01",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="Terminal date for the SIP (default: latest data / today)",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Optional comma-separated list of symbols (if omitted, auto-screens as of --asof)",
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
        default=None,
        help="Limit backtest to top N screened candidates",
    )
    parser.add_argument(
        "--catalog-dir",
        type=Path,
        default=None,
        help="Path to 1D NSE catalog directory",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format: table (default) or json",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional path to write JSON output report",
    )

    args = parser.parse_args(argv)

    try:
        asof_date = dt.date.fromisoformat(args.asof)
    except ValueError:
        err_console.print(f"[red]Error:[/red] Invalid date format for --asof: '{args.asof}'")
        return 1

    try:
        catalog_dir = find_catalog_path(args.catalog_dir)
    except FileNotFoundError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        return 1

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

    # 1. Resolve symbols: either explicit or auto-screened as of asof_date
    if args.symbols:
        target_symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        console.print(
            f"[dim]Running backtest on {len(target_symbols)} specified symbols from {asof_date} to {end_date}...[/dim]"
        )
    else:
        screened = screen_multibaggers(catalog_dir=catalog_dir, asof_date=asof_date)
        if args.top and args.top > 0:
            screened = screened[: args.top]
        target_symbols = [s["symbol"] for s in screened]
        console.print(
            f"[dim]Auto-screened {len(target_symbols)} candidates as of {asof_date}. Running SIP backtest till {end_date}...[/dim]"
        )

    if not target_symbols:
        err_console.print("[yellow]No candidates found for backtest.[/yellow]")
        return 0

    # 2. Run simulation across target symbols
    results: list[dict[str, Any]] = []
    portfolio_cfs: dict[dt.date, float] = {}

    for sym in target_symbols:
        bars = load_forward_bars(catalog_dir, sym, asof_date, end_date)
        sim = simulate_single_stock_sip(bars, args.sip_amount)
        if not sim:
            continue
        sim["symbol"] = sym
        results.append(sim)

        for d, cf in sim["cash_flows"]:
            portfolio_cfs[d] = portfolio_cfs.get(d, 0.0) + cf

    results.sort(key=lambda x: x["final_val"], reverse=True)

    # 3. Aggregate portfolio statistics
    tot_invested = sum(r["total_invested"] for r in results)
    tot_val = sum(r["final_val"] for r in results)
    tot_profit = tot_val - tot_invested
    port_multiple = tot_val / tot_invested if tot_invested > 0 else 1.0

    sorted_dates = sorted(portfolio_cfs.keys())
    port_cfs_list = [(d, portfolio_cfs[d]) for d in sorted_dates]
    port_xirr = calculate_xirr(port_cfs_list)

    summary = {
        "candidate_count": len(results),
        "asof_date": asof_date.isoformat(),
        "end_date": end_date.isoformat(),
        "sip_amount": args.sip_amount,
        "total_invested": tot_invested,
        "total_value": tot_val,
        "total_profit": tot_profit,
        "portfolio_multiple": port_multiple,
        "portfolio_xirr": port_xirr,
    }

    output_payload = {
        "summary": summary,
        "stocks": results,
    }

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(output_payload, indent=2) + "\n", encoding="utf-8")
        console.print(f"[green]Saved backtest report to {args.out}[/green]")

    if args.format == "json":
        print(json.dumps(output_payload, indent=2))
        return 0

    # Human-readable table
    console.print()
    table = Table(
        title=f"Out-of-Sample Forward SIP Backtest (₹{args.sip_amount:,.0f}/month from {asof_date} to {end_date})",
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

    for r in results:
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

    console.print()
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
    console.print(
        Panel(
            f"[bold]Total Capital Deployed :[/bold] ₹{summary['total_invested']:,.2f}  (across {summary['candidate_count']} stocks)\n"
            f"[bold]Current Portfolio Value:[/bold] [bold green]₹{summary['total_value']:,.2f}[/bold green]\n"
            f"[bold]Total Net Profit       :[/bold] [bold green]₹{summary['total_profit']:+,.2f}[/bold green] ([green]{p_profit_pct:+.1f}%[/green])\n"
            f"[bold]Overall SIP Multiple   :[/bold] [bold yellow]{summary['portfolio_multiple']:.2f}x[/bold yellow]\n"
            f"[bold]Portfolio Annual XIRR  :[/bold] [bold magenta]{p_xirr_str}[/bold magenta]",
            title="Portfolio Aggregate Performance",
            border_style="magenta",
        )
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
