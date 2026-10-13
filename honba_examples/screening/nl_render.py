"""honba_examples.screening.nl_render: Rich output for the natural-language screener."""

from __future__ import annotations

from typing import Any

from honba.display import Column, render_kv, render_table

__all__ = ["render_nl_result"]


def render_nl_result(res: dict[str, Any], query: str) -> None:
    """Print the translation summary, generated predicate, and the matches table."""
    summary_items = [
        ("User Prompt", res["query"]),
        ("LLM Backend", res["backend"]),
        ("Tier 1 Filter", res["filter_text"]),
        ("Point-in-Time Date", res["asof"]),
        ("Matched Stocks", f"{res['matched_count']} / {res['total_scanned']}"),
    ]

    if res["is_historical"] and res["basket_stats"]:
        stats = res["basket_stats"]
        summary_items.extend(
            [
                (
                    "Backtest Period",
                    f"{stats['asof_date']} → {stats['terminal_date']} ({stats['years_held']} yrs)",
                ),
                ("Basket Total Return", f"+{stats['basket_return_pct']}%"),
                ("Basket CAGR", f"+{stats['basket_cagr_pct']}%"),
                ("Win Rate", f"{stats['win_rate_pct']}%"),
                ("Multibaggers (≥2x)", stats["multibagger_count"]),
                ("Top Performer", stats["top_performer"]),
            ]
        )

    render_kv(summary_items, title="Natural Language Screener & Backtest Translation")

    print("\nGenerated Tier 2 Python Predicate:")
    for line in res["python_predicate"].splitlines():
        print(f"  {line}")
    print()

    if res["is_historical"]:
        cols = [
            Column("symbol", "Symbol", style="cyan"),
            Column(
                "entry_close",
                f"Entry ({res['asof']})",
                align="right",
                style="white",
                fmt=lambda v: f"₹{v:,.1f}" if v else "-",
            ),
            Column(
                "current_close",
                "CMP Today",
                align="right",
                style="white",
                fmt=lambda v: f"₹{v:,.1f}" if v else "-",
            ),
            Column(
                "market_cap_cr",
                "Entry Mcap",
                align="right",
                style="yellow",
                fmt=lambda v: f"₹{v:,.0f} Cr" if v else "-",
            ),
            Column(
                "return_pct",
                "Return",
                align="right",
                style="green",
                fmt=lambda v: f"+{v:,.1f}%" if v > 0 else f"{v:,.1f}%",
            ),
            Column(
                "cagr",
                "CAGR",
                align="right",
                style="bold green",
                fmt=lambda v: f"+{v:.1f}%" if v > 0 else f"{v:.1f}%",
            ),
            Column(
                "multiplier",
                "Multiplier",
                align="right",
                style="bold yellow",
                fmt=lambda v: f"{v:.2f}x" if v else "-",
            ),
            Column("badge", "Classification", style="magenta"),
        ]
        title = f"Historical Screener Backtest Results (As of {res['asof']} → Today)"
    else:
        cols = [
            Column("symbol", "Symbol", style="cyan"),
            Column(
                "close",
                "CMP (₹)",
                align="right",
                style="green",
                fmt=lambda v: f"₹{v:,.2f}" if v else "-",
            ),
            Column(
                "market_cap_cr",
                "Market Cap",
                align="right",
                style="bold yellow",
                fmt=lambda v: f"₹{v:,.0f} Cr" if v else "-",
            ),
            Column(
                "volume",
                "Volume",
                align="right",
                style="magenta",
                fmt=lambda v: f"{int(v):,}" if v else "-",
            ),
            Column(
                "sma50",
                "SMA 50 (₹)",
                align="right",
                style="yellow",
                fmt=lambda v: f"₹{v:,.2f}" if v else "-",
            ),
            Column(
                "rsi",
                "RSI",
                align="right",
                style="white",
                fmt=lambda v: f"{v:.1f}" if v else "-",
            ),
        ]
        title = f"Screener Matches for: '{query}'"

    render_table(res["rows"], cols, title=title)
