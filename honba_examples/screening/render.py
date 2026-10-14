"""honba_examples.screening.render: Rich terminal tables for chained screener -> SIP runs.

Pure presentation helpers: Stage 1 candidate tables (one layout per screener family) and the
Stage 2 forward-confirmation table with its summary panel. Candidates and portfolios are the
plain dicts produced by :mod:`honba_examples.screening` and :mod:`honba_examples.sip`.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

__all__ = [
    "render_backtest_table",
    "render_horizon_notice",
    "render_mutual_fund_table",
    "render_screener_table",
]


def render_screener_table(
    candidates: list[dict[str, Any]],
    asof_date: dt.date,
    strategy: str,
    train_years: int,
    graham_mode: str = "defensive",
    console: Console | None = None,
) -> None:
    """Render Rich terminal table for Stage 1 screened candidates."""
    if console is None:
        console = Console()
    if strategy == "multibagger":
        title = f"Stage 1 (In-Sample): Prospective 'Going-to-Be' Multibaggers as of {asof_date} ({train_years}-Yr Base Window)"
        table = Table(
            title=title,
            border_style="cyan",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
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
    elif strategy in ("graham", "intelligent_investor"):
        title = (
            f"Stage 1 (In-Sample): Benjamin Graham '{graham_mode.title()}' Value Candidates "
            f"as of {asof_date} ({train_years}-Yr Base Window)"
        )
        table = Table(
            title=title,
            border_style="cyan",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
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
        table.add_column("Div Yield", justify="right", style="bold magenta", no_wrap=True)

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
            comp_name = c.get("company_name", c["symbol"])
            table.add_row(
                c["symbol"],
                comp_name,
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
        console.print(table)
    elif strategy in ("net_net", "ncav"):
        title = (
            f"Stage 1 (In-Sample): Benjamin Graham Net-Net (NCAV) Candidates as of {asof_date} "
            f"({train_years}-Yr Base Window)"
        )
        table = Table(
            title=title,
            border_style="cyan",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
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
                f"₹{c.get('cur_assets_cr', 0):,.0f}Cr",
                f"₹{c.get('tot_liab_cr', 0):,.0f}Cr",
                f"₹{c.get('ncav_per_share', 0):,.2f}",
                p_ncav_s,
                mos_s,
                cr_s,
                f"₹{c.get('cash_per_share', 0):,.2f}",
                de_s,
                f"₹{c.get('cfo_cr', 0):,.0f}Cr",
            )
        console.print(table)
    else:
        title = f"Stage 1 (In-Sample): Screened {strategy.title()} Candidates as of {asof_date} ({train_years}-Yr Training Data)"
        table = Table(
            title=title,
            border_style="cyan",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("Symbol", style="bold cyan", no_wrap=True)
        table.add_column("Company", style="dim")
        table.add_column("Price (₹)", justify="right", no_wrap=True)
        table.add_column("52w Low (₹)", justify="right", style="dim", no_wrap=True)
        table.add_column("From 52w Low", justify="right", style="yellow", no_wrap=True)
        table.add_column(f"{train_years}-Yr Ret", justify="right", style="dim", no_wrap=True)
        table.add_column("Hit Low?", justify="center", style="bold green", no_wrap=True)
        table.add_column("Div Yield", justify="right", style="bold magenta", no_wrap=True)
        table.add_column("P/E", justify="right", no_wrap=True)
        table.add_column("ROCE %", justify="right", style="green", no_wrap=True)
        table.add_column("Debt/Eq", justify="right", no_wrap=True)

        for c in candidates:
            div_s = f"{c['div_yield_pct']:.2f}%" if c.get("div_yield_pct", 0) > 0 else "-"
            pe_s = f"{c['pe_ratio']:.1f}" if c.get("pe_ratio") is not None else "-"
            roce_s = f"{c['roce_pct']:.1f}%" if c.get("roce_pct") is not None else "-"
            de_s = f"{c['debt_to_equity']:.2f}" if c.get("debt_to_equity") is not None else "-"
            hit_s = "✓ Touch" if c.get("hit_52w_low_recently") else "Near"
            comp_name = c.get("company_name", c["symbol"])
            table.add_row(
                c["symbol"],
                comp_name,
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
    portfolio: dict[str, Any],
    asof_date: dt.date,
    end_date: dt.date,
    allocation: str = "52w-ratio",
    console: Console | None = None,
) -> None:
    """Render Rich terminal table and summary panel for Stage 2 out-of-sample forward backtest."""
    if console is None:
        console = Console()
    stocks: list[dict[str, Any]] = portfolio["stocks"]
    summary: dict[str, Any] = portfolio["summary"]
    init_capital = summary.get("initial_capital", 0.0)
    port_sip = summary.get("portfolio_monthly_sip", summary.get("monthly_sip", 0.0))
    tot_sip_invested = summary.get("total_sip_invested", 0.0)
    window_months = summary.get("window_months", 1)

    console.print()
    if init_capital > 0 and (tot_sip_invested > 0 or port_sip > 0):
        header = f"Stage 2 (Out-of-Sample Forward Confirmation): Initial Capital ₹{init_capital:,.0f} + Monthly SIP ₹{port_sip:,.0f}/mo ({asof_date} to {end_date})"
    elif init_capital > 0:
        header = f"Stage 2 (Out-of-Sample Forward Confirmation): Initial Capital ₹{init_capital:,.0f} Lump Sum ({asof_date} to {end_date})"
    else:
        header = f"Stage 2 (Out-of-Sample Forward Confirmation): Monthly SIP ₹{port_sip:,.0f}/mo ({asof_date} to {end_date})"

    table = Table(
        title=header,
        border_style="green",
        box=box.ROUNDED,
        header_style="bold green",
    )
    table.add_column("Symbol", style="bold cyan")
    table.add_column("Weight", justify="right", style="cyan")
    table.add_column("Months", justify="center")
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
            f"{r.get('weight_pct', 0.0):.1f}%",
            str(r["months"]),
            f"₹{r.get('sip_amount', 0.0):,.0f}",
            f"₹{r['total_invested']:,.0f}",
            f"₹{r['final_val']:,.0f}",
            f"[{profit_style}]₹{r['profit']:+,.0f}[/{profit_style}]",
            f"{r['sip_multiple']:.2f}x",
            f"{r['lump_multiple']:.2f}x",
            f"{r.get('peak_multiple', r['lump_multiple']):.2f}x",
            xirr_str,
            f"[{conf_style}]{conf}[/{conf_style}]",
        ]
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

    alloc_desc = (
        "Proximity to 52-Week High (Ratio to Low from 52w High)"
        if allocation == "52w-ratio"
        else ("Composite Multibagger Score" if allocation == "score" else "Equal Weighting (1/N)")
    )

    panel_lines = [
        f"[bold]In-Sample Screening Date :[/bold] [cyan]{asof_date}[/cyan]",
        f"[bold]Out-of-Sample Test Window:[/bold] [yellow]{asof_date}[/yellow] to [yellow]{end_date}[/yellow] ({window_months} months)",
        f"[bold]Capital Allocation Model :[/bold] [cyan]{alloc_desc}[/cyan]",
    ]
    if init_capital > 0:
        panel_lines.append(
            f"[bold]Initial Capital Deployed :[/bold] ₹{init_capital:,.2f} (invested on {asof_date} across {len(stocks)} stocks)"
        )
    if tot_sip_invested > 0 or port_sip > 0:
        panel_lines.append(
            f"[bold]Monthly SIP Installment  :[/bold] ₹{port_sip:,.2f}/month portfolio over {window_months} months"
        )
        panel_lines.append(
            f"[bold]Total SIP Invested       :[/bold] ₹{tot_sip_invested:,.2f} ({window_months} months × ₹{port_sip:,.2f})"
        )
    panel_lines.extend(
        [
            f"[bold]Total Capital Invested   :[/bold] [bold]₹{summary['total_invested']:,.2f}[/bold] (₹{init_capital:,.0f} initial + {window_months} × ₹{port_sip:,.0f} SIP)",
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
            box=box.ROUNDED,
        )
    )


def render_horizon_notice(
    n_candidates: int,
    asof_date: dt.date,
    latest_data_date: dt.date,
    strategy: str,
    top: int,
    console: Console | None = None,
) -> None:
    """Explain that Stage 2 cannot run because ``asof_date`` is at the catalog horizon."""
    if console is None:
        console = Console()
    console.print()
    console.print(
        Panel(
            f"Evaluation date ([cyan]{asof_date}[/cyan]) is at the horizon of the historical catalog (latest session: [yellow]{latest_data_date}[/yellow]).\n"
            "Forward out-of-sample data is not yet available for forward backtesting.\n\n"
            f"The [bold cyan]{n_candidates}[/bold cyan] screened candidates above represent prospective "
            "'going-to-be' investments identified for [bold green]live forward monitoring[/bold green]!\n\n"
            "[dim]💡 Tip: To simulate out-of-sample forward SIP confirmation, run with a historical evaluation date, e.g.:[/dim]\n"
            f"   [bold green]python backtesting/10_chained_screener_and_sip.py --strategy {strategy} --asof 2024-01-01 --top {top} --capital 100000 --sip 10000[/bold green]",
            title="Stage 2 Forward Confirmation Notice",
            border_style="yellow",
            box=box.ROUNDED,
        )
    )


def render_mutual_fund_table(
    candidates: list[dict[str, Any]],
    asof_date: dt.date,
    category: str = "all",
    console: Console | None = None,
) -> None:
    """Render Rich terminal table for screened mutual funds with equity lookthrough."""
    if console is None:
        console = Console()

    title = f"Mutual Fund Candidates ({category}) with Equity Lookthrough as of {asof_date}"
    table = Table(
        title=title,
        border_style="cyan",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    table.add_column("Code", style="bold cyan")
    table.add_column("Scheme Name", style="white")
    table.add_column("Category", style="dim")
    table.add_column("AUM (Cr)", justify="right", style="green")
    table.add_column("TER %", justify="right", style="yellow")
    table.add_column("Port ROCE", justify="right", style="bold green")
    table.add_column("Port P/E", justify="right", style="magenta")
    table.add_column("Cap Mix (L/M/S)", justify="center", style="cyan")
    table.add_column("Top Holdings (Lookthrough)", style="yellow")
    table.add_column("Score", justify="right", style="bold cyan")

    for c in candidates:
        roce_str = f"{c['portfolio_roce']:.1f}%" if c.get("portfolio_roce") is not None else "N/A"
        pe_str = f"{c['portfolio_pe']:.1f}x" if c.get("portfolio_pe") is not None else "N/A"
        l = c.get("large_cap_pct", 0)
        m = c.get("mid_cap_pct", 0)
        s = c.get("small_cap_pct", 0)
        cap_mix = f"{l:.0f}/{m:.0f}/{s:.0f}%"
        top_h = ", ".join(c.get("top_holdings", [])) or "None"

        table.add_row(
            c["scheme_code"],
            c["scheme_name"][:35],
            c["category"].replace("Equity:", ""),
            f"₹{c['aum_cr']:,.0f}",
            f"{c['ter_pct']:.2f}%",
            roce_str,
            pe_str,
            cap_mix,
            top_h,
            f"{c['score']:.1f}",
        )

    console.print(table)
