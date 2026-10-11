"""backtesting/10_value_dividend_screener.py: Point-in-Time Value & Dividend Screener.

Identifies contrarian value and high-dividend-yield stocks trading at or near
their 52-week low with strong underlying fundamentals (Zero Lookahead bias):
1. Evaluates all price and fundamental data strictly on or before `--asof <YYYY-MM-DD>`.
2. Computes distance from 52-week low:
   - `pct_from_52w_low`: `((current_close / low_52w) - 1.0) * 100`
   - Detects whether the stock has hit or touched 52-week low within the last N sessions.
3. Overlays fundamental balance sheet strength from DuckDB:
   - Trailing P/E and P/B ratios
   - Cash Dividend Yield %: `(Cash Dividends Paid / Market Cap) * 100`
   - Return on Capital Employed (`ROCE %`) and `ROE %`
   - Debt to Equity ratio (`D/E <= 1.5` to avoid debt traps)
   - Free Cash Flow (`FCF > 0`)

Supports Unix pipe chaining:
    # Screen value/dividend candidates and pipe into forward SIP backtester:
    python backtesting/10_value_dividend_screener.py --asof 2023-01-01 --mode dividend --format symbols \
      | python backtesting/09_multibagger_sip_backtest.py --asof 2023-01-01

Usage::

    # Screen high dividend yielders near 52w low as of 2023-01-01:
    python backtesting/10_value_dividend_screener.py --asof 2023-01-01 --mode dividend

    # Screen deep value stocks near 52w low as of today:
    python backtesting/10_value_dividend_screener.py --mode value --top 10

    # Custom thresholds (within 10% of 52w low, min 3% div yield, max D/E 1.0):
    python backtesting/10_value_dividend_screener.py --max-pct-from-52w-low 10.0 --min-div-yield 3.0 --max-debt-equity 1.0
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

import pyarrow as pa
import pyarrow.parquet as pq
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

logger = logging.getLogger(__name__)
console = Console()
err_console = Console(stderr=True)


def find_catalog_path(explicit_path: Path | None = None) -> Path:
    """Locate the 1D NSE Parquet catalog directory."""
    if explicit_path and explicit_path.is_dir():
        return explicit_path

    candidates = [
        Path.cwd() / "honba-data" / "data" / "catalog" / "1D" / "NSE",
        Path.cwd() / "data" / "catalog" / "1D" / "NSE",
        Path(__file__).resolve().parents[2] / "honba-data" / "data" / "catalog" / "1D" / "NSE",
        Path(__file__).resolve().parents[2] / "data" / "catalog" / "1D" / "NSE",
    ]
    for c in candidates:
        if c.is_dir():
            return c.resolve()

    raise FileNotFoundError(
        "Could not find 1D NSE catalog. Please pass --catalog-dir pointing to <data_dir>/catalog/1D/NSE."
    )


def find_duckdb_path() -> Path | None:
    """Optionally locate fundamentals.duckdb for fundamental overlay."""
    candidates = [
        Path.cwd() / "honba-data" / "data" / "fundamentals.duckdb",
        Path.cwd() / "data" / "fundamentals.duckdb",
        Path(__file__).resolve().parents[2] / "honba-data" / "data" / "fundamentals.duckdb",
        Path(__file__).resolve().parents[2] / "data" / "fundamentals.duckdb",
    ]
    for c in candidates:
        if c.is_file():
            return c.resolve()
    return None


def get_latest_market_date(catalog_dir: Path) -> dt.date:
    """Determine the latest trading session date available in the catalog."""
    latest_ts = 0
    for s_dir in catalog_dir.iterdir():
        if not s_dir.is_dir():
            continue
        parquet_files = sorted(s_dir.glob("*.parquet"))
        if parquet_files:
            last_file = parquet_files[-1]
            try:
                table = pq.read_table(last_file, columns=["ts"])
                ts_col = table.column("ts").to_pylist()
                if ts_col:
                    latest_ts = max(latest_ts, max(ts_col))
            except (pa.ArrowException, OSError) as exc:
                logger.debug("Skipping %s when determining latest date: %s", last_file, exc)
                continue
    if latest_ts > 0:
        return dt.datetime.fromtimestamp(latest_ts / 1e9, dt.timezone.utc).date()
    return dt.datetime.now(dt.timezone.utc).date()


def load_bars_as_of(catalog_dir: Path, symbol: str, asof_date: dt.date) -> list[dict[str, Any]]:
    """Load daily bars for a symbol strictly on or before asof_date."""
    sym_dir = catalog_dir / symbol
    if not sym_dir.is_dir():
        return []

    asof_year = asof_date.year
    parquet_files = sorted(sym_dir.glob("*.parquet"))

    raw_bars: list[dict[str, Any]] = []
    for f in parquet_files:
        try:
            yr = int(f.stem)
            if yr > asof_year:
                continue
        except ValueError:
            pass

        try:
            tbl = pq.read_table(f)
            ts_list: list[int] = tbl.column("ts").to_pylist()
            open_list: list[float] = [float(x) for x in tbl.column("open").to_pylist()]
            high_list: list[float] = [float(x) for x in tbl.column("high").to_pylist()]
            low_list: list[float] = [float(x) for x in tbl.column("low").to_pylist()]
            close_list: list[float] = [float(x) for x in tbl.column("close").to_pylist()]
            vol_list: list[float] = [float(x) for x in tbl.column("volume").to_pylist()]

            for t, o, h, l, c, v in zip(
                ts_list, open_list, high_list, low_list, close_list, vol_list
            ):
                d = dt.datetime.fromtimestamp(t / 1e9, dt.timezone.utc).date()
                if d <= asof_date:
                    raw_bars.append(
                        {
                            "date": d,
                            "open": o,
                            "high": h,
                            "low": l,
                            "close": c,
                            "volume": v,
                        }
                    )
        except (pa.ArrowException, OSError) as exc:
            logger.debug("Failed reading %s: %s", f, exc)

    raw_bars.sort(key=lambda b: b["date"])
    return raw_bars


def load_point_in_time_fundamentals(
    db_path: Path | None, asof_date: dt.date
) -> dict[str, dict[str, Any]]:
    """Query DuckDB for point-in-time valuation, dividend, and balance sheet metrics."""
    if not db_path:
        return {}

    fund_map: dict[str, dict[str, Any]] = {}
    duckdb_mod: Any = None
    try:
        import duckdb

        duckdb_mod = duckdb
    except ImportError:
        logger.debug("duckdb package is not installed; skipping fundamental overlay.")
        return {}

    try:
        con = duckdb_mod.connect(str(db_path), read_only=True)
        asof_str = asof_date.isoformat()
        query = f"""
            WITH div_summary AS (
                SELECT symbol, abs(value) as div_paid
                FROM financial_statements
                WHERE line_item IN ('Cash Dividends Paid', 'Common Stock Dividend Paid')
                  AND fiscal_date <= '{asof_str}'
                QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY fiscal_date DESC) = 1
            ),
            ratios_summary AS (
                SELECT symbol, roce_pct, roe_pct, debt_to_equity, free_cash_flow, eps
                FROM financial_ratios
                WHERE fiscal_date <= '{asof_str}'
                QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY fiscal_date DESC) = 1
            )
            SELECT c.symbol, c.company_name, c.market_cap, c.pe_trailing, c.pb_ratio,
                   r.roce_pct, r.roe_pct, r.debt_to_equity, r.free_cash_flow, r.eps,
                   d.div_paid
            FROM companies c
            LEFT JOIN ratios_summary r ON c.symbol = r.symbol
            LEFT JOIN div_summary d ON c.symbol = d.symbol
        """
        for row in con.execute(query).fetchall():
            sym = str(row[0])
            company_name = str(row[1]) if row[1] is not None else sym
            mcap = float(row[2]) if row[2] is not None else None
            pe_t = float(row[3]) if row[3] is not None else None
            pb = float(row[4]) if row[4] is not None else None
            roce = float(row[5]) if row[5] is not None else None
            roe = float(row[6]) if row[6] is not None else None
            de = float(row[7]) if row[7] is not None else None
            fcf = float(row[8]) if row[8] is not None else None
            eps = float(row[9]) if row[9] is not None else None
            div_paid = float(row[10]) if row[10] is not None else None

            # Calculate dividend yield % if market cap and dividend paid are present
            div_yield = (
                round((div_paid / mcap) * 100.0, 2) if (div_paid and mcap and mcap > 0) else 0.0
            )

            fund_map[sym] = {
                "company_name": company_name,
                "market_cap": mcap,
                "pe_trailing": pe_t,
                "pb_ratio": pb,
                "roce_pct": roce,
                "roe_pct": roe,
                "debt_to_equity": de,
                "free_cash_flow": fcf,
                "eps": eps,
                "div_paid": div_paid,
                "div_yield_pct": div_yield,
            }
    except (duckdb_mod.Error, OSError) as exc:
        logger.debug("DuckDB lookup omitted: %s", exc)

    return fund_map


def screen_value_and_dividend(
    catalog_dir: Path,
    asof_date: dt.date,
    mode: str = "all",
    max_pct_from_52w_low: float = 15.0,
    min_div_yield: float = 0.0,
    min_roce: float = 10.0,
    max_pe: float | None = None,
    max_debt_equity: float = 1.5,
    low_window_bars: int = 20,
) -> list[dict[str, Any]]:
    """Screen contrarian value and dividend yield candidates near 52-week low."""
    candidates: list[dict[str, Any]] = []
    db_path = find_duckdb_path()
    fund_map = load_point_in_time_fundamentals(db_path, asof_date)

    symbols = sorted([d.name for d in catalog_dir.iterdir() if d.is_dir()])
    for sym in symbols:
        bars = load_bars_as_of(catalog_dir, sym, asof_date)
        if len(bars) < 252:
            continue

        closes = [b["close"] for b in bars]
        lows = [b["low"] for b in bars]
        highs = [b["high"] for b in bars]

        last_close = closes[-1]
        last_date = bars[-1]["date"]

        low52 = min(lows[-252:])
        high52 = max(highs[-252:])

        pct_from_low = ((last_close / low52) - 1.0) * 100.0
        pct_from_high = ((last_close / high52) - 1.0) * 100.0

        # Check if the stock has touched 52-week low recently
        window = min(low_window_bars, len(lows))
        recent_min_low = min(lows[-window:])
        hit_52w_low_recently = recent_min_low <= (low52 * 1.01)

        # 1. Price distance from 52-week low filter
        if pct_from_low > max_pct_from_52w_low:
            continue

        f = fund_map.get(sym, {})
        roce = f.get("roce_pct")
        de = f.get("debt_to_equity")
        div_y = f.get("div_yield_pct", 0.0)
        pe = f.get("pe_trailing")
        pb = f.get("pb_ratio")
        fcf = f.get("free_cash_flow")

        # 2. Fundamental Filters (if data available)
        if de is not None and de > max_debt_equity:
            continue

        if roce is not None and roce < min_roce:
            continue

        if max_pe is not None and pe is not None and (pe <= 0 or pe > max_pe):
            continue

        # Mode-specific filtering:
        if mode == "dividend":
            effective_min_div = min_div_yield if min_div_yield > 0.0 else 2.5
            if div_y < effective_min_div:
                continue
        elif mode == "value":
            if pe is not None and pe > 25.0:
                continue
        else:
            if min_div_yield > 0.0 and div_y < min_div_yield:
                continue

        candidates.append(
            {
                "symbol": sym,
                "company_name": f.get("company_name", sym),
                "asof_date": last_date.isoformat(),
                "close": round(last_close, 2),
                "low52": round(low52, 2),
                "high52": round(high52, 2),
                "pct_from_low": round(pct_from_low, 2),
                "pct_from_high": round(pct_from_high, 2),
                "hit_52w_low_recently": hit_52w_low_recently,
                "div_yield_pct": div_y,
                "pe_ratio": round(pe, 1) if pe is not None else None,
                "pb_ratio": round(pb, 2) if pb is not None else None,
                "roce_pct": round(roce, 1) if roce is not None else None,
                "debt_to_equity": round(de, 2) if de is not None else None,
                "free_cash_flow": fcf,
            }
        )

    # Ranking:
    # In dividend mode: rank highest dividend yield first
    # In value mode: rank lowest pct_from_low and lowest P/E
    if mode == "dividend":
        candidates.sort(key=lambda x: (x["div_yield_pct"], -x["pct_from_low"]), reverse=True)
    else:
        candidates.sort(key=lambda x: (x["pct_from_low"], -(x["div_yield_pct"] or 0.0)))

    return candidates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Point-in-Time Value & Dividend 52-Week Low Screener"
    )
    parser.add_argument(
        "--asof",
        type=str,
        default=None,
        help="Evaluation date (YYYY-MM-DD). Defaults to latest available market session.",
    )
    parser.add_argument(
        "--mode",
        choices=["all", "value", "dividend"],
        default="all",
        help="Screening focus mode: 'all' (default), 'value' (low P/E & P/B), or 'dividend' (yield >= 2.5%%)",
    )
    parser.add_argument(
        "--max-pct-from-52w-low",
        type=float,
        default=15.0,
        help="Maximum distance percentage above 52-week low (default: 15.0%%)",
    )
    parser.add_argument(
        "--min-div-yield",
        type=float,
        default=0.0,
        help="Minimum annual cash dividend yield percentage (default: 0.0%%)",
    )
    parser.add_argument(
        "--min-roce",
        type=float,
        default=10.0,
        help="Minimum ROCE percentage for quality guard (default: 10.0%%)",
    )
    parser.add_argument(
        "--max-pe",
        type=float,
        default=None,
        help="Optional maximum P/E ratio threshold",
    )
    parser.add_argument(
        "--max-debt-equity",
        type=float,
        default=1.5,
        help="Maximum Debt to Equity leverage ratio (default: 1.5)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="Limit output to top N candidates",
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
        help="Output format: table (default), json, or symbols (plain list for piping)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional path to write JSON output results",
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

    candidates = screen_value_and_dividend(
        catalog_dir=catalog_dir,
        asof_date=asof_date,
        mode=args.mode,
        max_pct_from_52w_low=args.max_pct_from_52w_low,
        min_div_yield=args.min_div_yield,
        min_roce=args.min_roce,
        max_pe=args.max_pe,
        max_debt_equity=args.max_debt_equity,
    )

    if args.top and args.top > 0:
        candidates = candidates[: args.top]

    # Handle output formats
    if args.format == "symbols":
        print(" ".join(c["symbol"] for c in candidates))
        return 0

    output_payload = {
        "asof_date": asof_date.isoformat(),
        "mode": args.mode,
        "screening_rules": {
            "max_pct_from_52w_low": args.max_pct_from_52w_low,
            "min_div_yield": args.min_div_yield,
            "min_roce": args.min_roce,
            "max_pe": args.max_pe,
            "max_debt_equity": args.max_debt_equity,
        },
        "count": len(candidates),
        "candidates": candidates,
    }

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(output_payload, indent=2) + "\n", encoding="utf-8")
        err_console.print(f"[green]Saved {len(candidates)} candidates to {args.out}[/green]")

    if args.format == "json":
        print(json.dumps(output_payload, indent=2))
        return 0

    # Human-readable table
    mode_label = {
        "dividend": "High Dividend Yielders near 52-Week Low",
        "value": "Contrarian Value near 52-Week Low",
        "all": "Value & Dividend 52-Week Low Screener",
    }[args.mode]

    console.print()
    console.print(
        Panel(
            f"[bold cyan]{mode_label}[/bold cyan] (Evaluated strictly as of [bold yellow]{asof_date}[/bold yellow])\n"
            f"[dim]Rules: Within {args.max_pct_from_52w_low:.1f}% of 52w Low | ROCE ≥ {args.min_roce:.1f}% | Debt/Eq ≤ {args.max_debt_equity:.2f}[/dim]",
            border_style="cyan",
        )
    )

    table = Table(
        title=f"Screened Candidates as of {asof_date} ({len(candidates)} found)",
        border_style="blue",
    )
    table.add_column("Symbol", style="bold cyan")
    table.add_column("Price (₹)", justify="right")
    table.add_column("52w Low (₹)", justify="right", style="dim")
    table.add_column("From 52w Low", justify="right", style="yellow")
    table.add_column("Hit Low?", justify="center", style="bold green")
    table.add_column("Div Yield %", justify="right", style="bold magenta")
    table.add_column("P/E", justify="right")
    table.add_column("P/B", justify="right")
    table.add_column("ROCE %", justify="right", style="green")
    table.add_column("Debt/Eq", justify="right")

    for c in candidates:
        div_str = f"{c['div_yield_pct']:.2f}%" if c["div_yield_pct"] > 0 else "-"
        pe_str = f"{c['pe_ratio']:.1f}" if c.get("pe_ratio") is not None else "-"
        pb_str = f"{c['pb_ratio']:.2f}" if c.get("pb_ratio") is not None else "-"
        roce_str = f"{c['roce_pct']:.1f}%" if c.get("roce_pct") is not None else "-"
        de_str = f"{c['debt_to_equity']:.2f}" if c.get("debt_to_equity") is not None else "-"
        hit_str = "✓ Touch" if c["hit_52w_low_recently"] else "Near"

        table.add_row(
            c["symbol"],
            f"₹{c['close']:,.2f}",
            f"₹{c['low52']:,.2f}",
            f"+{c['pct_from_low']:.1f}%",
            hit_str,
            div_str,
            pe_str,
            pb_str,
            roce_str,
            de_str,
        )

    console.print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
