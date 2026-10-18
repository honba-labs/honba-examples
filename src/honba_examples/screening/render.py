"""honba_examples.screening.render: terminal tables for chained screener -> SIP runs.

Pure presentation helpers: Stage 1 candidate tables (one column layout per screener family) and
the Stage 2 forward-confirmation table with its summary panel. Candidates and portfolios are
the plain dicts produced by :mod:`honba_examples.screening` and :mod:`honba_examples.sip`.

Every table renders through :func:`honba.display.render_table` using the shared palette in
:mod:`honba_examples.display_style`, so a strategy's candidates look the same here as they do
printed directly by its standalone screener script (e.g. ``screening/01_multibagger_screener.py``).
The Stage 2 summary block is a Rich ``Panel``, not a table, and stays hand-built -- it is a
different widget, out of scope for table unification.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, TextIO, cast

from honba.display import Column, render_table
from rich import box
from rich.console import Console
from rich.panel import Panel

from honba_examples.display_style import SCORE, SECONDARY, SURGE, SYMBOL, pnl_style_fn
from honba_examples.sip import sip_data_notes

__all__ = [
    "render_backtest_table",
    "render_horizon_notice",
    "render_mutual_fund_table",
    "render_screener_table",
]


def _inr(v: Any) -> str:
    return f"₹{float(v):,.2f}"


def _inr_cr(v: Any) -> str:
    return f"₹{float(v):,.0f}Cr"


def _inr0(v: Any) -> str:
    return f"₹{float(v):,.0f}"


def _pct1(v: Any) -> str:
    return f"{float(v):.1f}%"


def _signed_pct1(v: Any) -> str:
    return f"{float(v):+.1f}%"


def _with_company_fallback(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Candidates with ``company_name`` defaulted to ``symbol`` (display only)."""
    return [{**c, "company_name": c.get("company_name", c["symbol"])} for c in candidates]


def _console_stream(console: Console | None) -> TextIO | None:
    return cast(TextIO, console.file) if console is not None else None


def render_screener_table(
    candidates: list[dict[str, Any]],
    asof_date: dt.date,
    strategy: str,
    train_years: int,
    graham_mode: str = "defensive",
    console: Console | None = None,
) -> None:
    """Render Stage 1 screened candidates as a table."""
    out = _console_stream(console)

    if strategy == "multibagger":
        title = f"Stage 1 (In-Sample): Prospective 'Going-to-Be' Multibaggers as of {asof_date} ({train_years}-Yr Base Window)"
        columns = [
            Column("symbol", "Symbol", style=SYMBOL),
            Column("close", "Price (₹)", align="right", fmt=_inr),
            Column("ret_1y", "1-Yr Ret", align="right", fmt=_signed_pct1, style_fn=pnl_style_fn),
            Column("ret_3m", "3-Mo Surge", align="right", fmt=_signed_pct1, style_fn=pnl_style_fn),
            Column(
                "pct_from_high",
                "From 52w High",
                align="right",
                fmt=_signed_pct1,
                style_fn=pnl_style_fn,
            ),
            Column(
                "vol_surge",
                "Vol Surge",
                align="right",
                fmt=lambda v: f"{v:.2f}x",
                style=SURGE,
            ),
            Column("sma200", "200 SMA", align="right", fmt=_inr, style=SECONDARY),
            Column("roce_pct", "ROCE %", align="right", fmt=_pct1, style="green"),
            Column(
                "debt_to_equity",
                "Debt/Eq",
                align="right",
                fmt=lambda v: f"{v:.2f}",
                style=SECONDARY,
            ),
            Column("mb_score", "MB Score", align="right", fmt=lambda v: f"{v:.1f}", style=SCORE),
        ]
        rows = [{**c, "ret_3m": c.get("ret_3m", 0.0)} for c in candidates]
        render_table(rows, columns, title=title, out=out)
        return

    if strategy in ("graham", "intelligent_investor"):
        title = (
            f"Stage 1 (In-Sample): Benjamin Graham '{graham_mode.title()}' Value Candidates "
            f"as of {asof_date} ({train_years}-Yr Base Window)"
        )
        columns = [
            Column("symbol", "Symbol", style=SYMBOL),
            Column("company_name", "Company", style=SECONDARY),
            Column("close", "Price (₹)", align="right", fmt=_inr),
            Column("eps", "EPS (₹)", align="right", fmt=_inr, style="green"),
            Column("bvps", "BVPS (₹)", align="right", fmt=_inr, style=SECONDARY),
            Column("pe_ratio", "P/E", align="right", fmt=lambda v: f"{v:.1f}"),
            Column("pb_ratio", "P/B", align="right", fmt=lambda v: f"{v:.2f}"),
            Column("graham_product", "P/E×P/B", align="right", fmt=lambda v: f"{v:.1f}"),
            Column("graham_num", "Graham Val (₹)", align="right", fmt=_inr),
            Column(
                "margin_of_safety_pct",
                "Margin of Safety",
                align="right",
                fmt=_signed_pct1,
                style_fn=pnl_style_fn,
            ),
            Column("current_ratio", "Cur Ratio", align="right", fmt=lambda v: f"{v:.2f}"),
            Column(
                "debt_to_equity",
                "Debt/Eq",
                align="right",
                fmt=lambda v: f"{v:.2f}",
                style=SECONDARY,
            ),
            Column(
                "div_yield_pct",
                "Div Yield",
                align="right",
                fmt=lambda v: f"{v:.2f}%" if v > 0 else "-",
                style="bold magenta",
            ),
        ]
        render_table(_with_company_fallback(candidates), columns, title=title, out=out)
        return

    if strategy in ("net_net", "ncav"):
        title = (
            f"Stage 1 (In-Sample): Benjamin Graham Net-Net (NCAV) Candidates as of {asof_date} "
            f"({train_years}-Yr Base Window)"
        )
        columns = [
            Column("symbol", "Symbol", style=SYMBOL),
            Column("company_name", "Company", style=SECONDARY),
            Column("close", "Price (₹)", align="right", fmt=_inr),
            Column("cur_assets_cr", "Cur Assets", align="right", fmt=_inr_cr, style=SECONDARY),
            Column("tot_liab_cr", "Tot Liab", align="right", fmt=_inr_cr, style=SECONDARY),
            Column("ncav_per_share", "NCAV/Sh (₹)", align="right", fmt=_inr),
            Column(
                "price_to_ncav",
                "P/NCAV",
                align="right",
                fmt=lambda v: f"{v:.2f}x",
                style="bold green",
            ),
            Column(
                "ncav_margin_of_safety_pct",
                "NCAV MoS",
                align="right",
                fmt=_signed_pct1,
                style_fn=pnl_style_fn,
            ),
            Column("current_ratio", "Cur Ratio", align="right", fmt=lambda v: f"{v:.2f}"),
            Column("cash_per_share", "Cash/Sh (₹)", align="right", fmt=_inr, style="magenta"),
            Column(
                "debt_to_equity",
                "Debt/Eq",
                align="right",
                fmt=lambda v: f"{v:.2f}",
                style=SECONDARY,
            ),
            Column("cfo_cr", "CFO (₹Cr)", align="right", fmt=_inr_cr, style="green"),
        ]
        rows = [
            {
                **c,
                "cur_assets_cr": c.get("cur_assets_cr", 0),
                "tot_liab_cr": c.get("tot_liab_cr", 0),
                "ncav_per_share": c.get("ncav_per_share", 0),
                "cash_per_share": c.get("cash_per_share", 0),
                "cfo_cr": c.get("cfo_cr", 0),
            }
            for c in candidates
        ]
        render_table(_with_company_fallback(rows), columns, title=title, out=out)
        return

    title = f"Stage 1 (In-Sample): Screened {strategy.title()} Candidates as of {asof_date} ({train_years}-Yr Training Data)"
    columns = [
        Column("symbol", "Symbol", style=SYMBOL),
        Column("company_name", "Company", style=SECONDARY),
        Column("close", "Price (₹)", align="right", fmt=_inr),
        Column("low52", "52w Low (₹)", align="right", fmt=_inr, style=SECONDARY),
        Column(
            "pct_from_low", "From 52w Low", align="right", fmt=lambda v: f"+{v:.1f}%", style=SURGE
        ),
        Column(
            "train_ret_pct",
            f"{train_years}-Yr Ret",
            align="right",
            fmt=_signed_pct1,
            style=SECONDARY,
        ),
        Column(
            "hit_52w_low_recently",
            "Hit Low?",
            align="center",
            fmt=lambda v: "✓ Touch" if v else "Near",
            style="bold green",
        ),
        Column(
            "div_yield_pct",
            "Div Yield",
            align="right",
            fmt=lambda v: f"{v:.2f}%" if v > 0 else "-",
            style="bold magenta",
        ),
        Column("pe_ratio", "P/E", align="right", fmt=lambda v: f"{v:.1f}"),
        Column("pb_ratio", "P/B", align="right", fmt=lambda v: f"{v:.2f}"),
        Column("roce_pct", "ROCE %", align="right", fmt=_pct1, style="green"),
        Column("debt_to_equity", "Debt/Eq", align="right", fmt=lambda v: f"{v:.2f}"),
        Column(
            "free_cash_flow",
            "FCF",
            align="center",
            fmt=lambda v: "+" if v > 0 else "-",
        ),
    ]
    render_table(_with_company_fallback(candidates), columns, title=title, out=out)


def _confirmation_style(conf: Any) -> str | None:
    text = str(conf)
    if "Multibagger" in text:
        return "bold green"
    if "Outperformer" in text:
        return "green"
    return "dim"


def render_backtest_table(
    portfolio: dict[str, Any],
    asof_date: dt.date,
    end_date: dt.date,
    allocation: str = "52w-ratio",
    console: Console | None = None,
) -> None:
    """Render the Stage 2 out-of-sample forward backtest table and its summary panel."""
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

    columns = [
        Column("symbol", "Symbol", style=SYMBOL),
        Column("weight_pct", "Weight", align="right", fmt=lambda v: f"{v:.1f}%", style="cyan"),
        Column("months", "Months", align="center"),
        Column("sip_amount", "SIP / Mo", align="right", fmt=_inr0, style=SECONDARY),
        Column("total_invested", "Total Invested", align="right", fmt=_inr0),
        Column("final_val", "Final Valuation", align="right", fmt=_inr0, style="bold green"),
        Column(
            "profit",
            "Net Profit (₹)",
            align="right",
            fmt=lambda v: f"₹{v:+,.0f}",
            style_fn=pnl_style_fn,
        ),
        Column(
            "sip_multiple",
            "SIP Mult",
            align="right",
            fmt=lambda v: f"{v:.2f}x",
            style="bold yellow",
        ),
        Column(
            "lump_multiple",
            "Lump Mult",
            align="right",
            fmt=lambda v: f"{v:.2f}x",
            style="bold green",
        ),
        Column(
            "peak_multiple", "Peak Mult", align="right", fmt=lambda v: f"{v:.2f}x", style="magenta"
        ),
        Column(
            "xirr_pct",
            "XIRR (p.a.)",
            align="right",
            fmt=lambda v: f"{v:+.1f}%",
            style="bold magenta",
        ),
        Column(
            "confirmation",
            "Confirmation Status",
            align="center",
            style_fn=_confirmation_style,
        ),
    ]
    rows = [
        {
            **r,
            "weight_pct": r.get("weight_pct", 0.0),
            "sip_amount": r.get("sip_amount", 0.0),
            "peak_multiple": r.get("peak_multiple", r["lump_multiple"]),
            "confirmation": r.get("confirmation", "N/A"),
        }
        for r in stocks
    ]
    confirmed_count = sum(1 for r in rows if "Multibagger" in str(r["confirmation"]))
    render_table(rows, columns, title=header, out=cast(TextIO, console.file))

    for note in sip_data_notes(summary):
        console.print(f"[yellow]⚠ {note}[/yellow]")

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
    """Render screened mutual funds with equity lookthrough."""
    out = _console_stream(console)
    title = f"Mutual Fund Candidates ({category}) with Equity Lookthrough as of {asof_date}"
    columns = [
        Column("scheme_code", "Code", style=SYMBOL),
        Column("scheme_name", "Scheme Name", fmt=lambda v: v[:35], style="white"),
        Column("category", "Category", fmt=lambda v: v.replace("Equity:", ""), style=SECONDARY),
        Column("aum_cr", "AUM (Cr)", align="right", fmt=lambda v: f"₹{v:,.0f}", style="green"),
        Column("ter_pct", "TER %", align="right", fmt=lambda v: f"{v:.2f}%"),
        Column(
            "portfolio_roce",
            "Port ROCE",
            align="right",
            fmt=lambda v: f"{v:.1f}%",
            style="bold green",
        ),
        Column(
            "portfolio_pe", "Port P/E", align="right", fmt=lambda v: f"{v:.1f}x", style="magenta"
        ),
        Column("cap_mix", "Cap Mix (L/M/S)", align="center", style="cyan"),
        Column("top_holdings", "Top Holdings (Lookthrough)", style=SECONDARY),
        Column("score", "Score", align="right", fmt=lambda v: f"{v:.1f}", style=SCORE),
    ]
    rows = [
        {
            **c,
            "cap_mix": (
                f"{c.get('large_cap_pct', 0):.0f}/{c.get('mid_cap_pct', 0):.0f}/"
                f"{c.get('small_cap_pct', 0):.0f}%"
            ),
            "top_holdings": ", ".join(c.get("top_holdings", [])) or "None",
        }
        for c in candidates
    ]
    render_table(rows, columns, title=title, out=out)
