"""backtesting/08_multibagger_screener.py: Point-in-Time Multibagger Screener.

Identifies potential multibagger candidates as of any given date (historical or today)
with ZERO lookahead bias:
1. Evaluates all price and fundamental data strictly on or before `--asof <YYYY-MM-DD>`.
2. Applies institutional Stage 2 trend criteria:
   - 1-year relative strength > threshold (default: 35.0%)
   - Trading above 200-day moving average
   - 200-day moving average in a rising trend
   - Consolidation within threshold of 52-week High (default: within 20%)
   - Overlays DuckDB balance sheet metrics (ROCE %, Debt/Equity) when available.

Usage::

    # Screen candidates as of 2023-01-01:
    python backtesting/08_multibagger_screener.py --asof 2023-01-01

    # Screen candidates as of today (latest available market data):
    python backtesting/08_multibagger_screener.py

    # Output plain symbols list (for piping into other tools):
    python backtesting/08_multibagger_screener.py --asof 2023-01-01 --format symbols
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


def screen_multibaggers(
    catalog_dir: Path,
    asof_date: dt.date,
    min_return_1y: float = 35.0,
    max_pct_from_high: float = 20.0,
) -> list[dict[str, Any]]:
    """Screen candidate multibaggers strictly as of asof_date (Zero Lookahead)."""
    candidates: list[dict[str, Any]] = []

    # Optional DuckDB metadata lookup
    db_path = find_duckdb_path()
    fund_ratios: dict[str, dict[str, Any]] = {}
    if db_path:
        duckdb_mod: Any = None
        try:
            import duckdb

            duckdb_mod = duckdb
        except ImportError:
            logger.debug("duckdb package is not installed; skipping fundamental ratio overlay.")

        if duckdb_mod is not None:
            try:
                con = duckdb_mod.connect(str(db_path), read_only=True)
                asof_str = asof_date.isoformat()
                query = f"""
                    SELECT r.symbol, r.roce_pct, r.roe_pct, r.debt_to_equity
                    FROM financial_ratios r
                    WHERE r.fiscal_date <= '{asof_str}'
                    ORDER BY r.fiscal_date DESC
                """
                for row in con.execute(query).fetchall():
                    sym = str(row[0])
                    if sym not in fund_ratios:
                        fund_ratios[sym] = {
                            "roce_pct": float(row[1]) if row[1] is not None else None,
                            "roe_pct": float(row[2]) if row[2] is not None else None,
                            "debt_to_equity": float(row[3]) if row[3] is not None else None,
                        }
            except (duckdb_mod.Error, OSError) as exc:
                logger.debug("DuckDB lookup omitted: %s", exc)

    symbols = sorted([d.name for d in catalog_dir.iterdir() if d.is_dir()])
    for sym in symbols:
        bars = load_bars_as_of(catalog_dir, sym, asof_date)
        if len(bars) < 252:
            continue

        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]

        last_close = closes[-1]
        last_date = bars[-1]["date"]

        sma50 = sum(closes[-50:]) / 50.0
        sma200 = sum(closes[-200:]) / 200.0
        high52 = max(highs[-252:])

        # 1-year relative strength return
        close_1y_prior = closes[-252]
        ret_1y = ((last_close / close_1y_prior) - 1.0) * 100.0

        # Consolidation distance from 52-week high
        pct_from_high = ((last_close / high52) - 1.0) * 100.0

        # Rising 200-day moving average slope
        sma200_prior = sum(closes[-250:-50]) / 200.0 if len(closes) >= 250 else sma200
        sma200_rising = sma200 >= sma200_prior

        # Qualification rules:
        if (
            ret_1y >= min_return_1y
            and last_close >= sma200
            and sma200_rising
            and pct_from_high >= -abs(max_pct_from_high)
        ):
            f_info = fund_ratios.get(sym, {})
            candidates.append(
                {
                    "symbol": sym,
                    "asof_date": last_date.isoformat(),
                    "close": round(last_close, 2),
                    "ret_1y": round(ret_1y, 2),
                    "pct_from_high": round(pct_from_high, 2),
                    "sma50": round(sma50, 2),
                    "sma200": round(sma200, 2),
                    "sma200_rising": sma200_rising,
                    "above_sma50": last_close >= sma50,
                    "roce_pct": f_info.get("roce_pct"),
                    "roe_pct": f_info.get("roe_pct"),
                    "debt_to_equity": f_info.get("debt_to_equity"),
                }
            )

    candidates.sort(key=lambda x: x["ret_1y"], reverse=True)
    return candidates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Point-in-Time Multibagger Screener (Zero Lookahead)"
    )
    parser.add_argument(
        "--asof",
        type=str,
        default=None,
        help="Evaluation date (YYYY-MM-DD). Defaults to latest available market session.",
    )
    parser.add_argument(
        "--min-return-1y",
        type=float,
        default=35.0,
        help="Minimum 1-year return relative strength threshold (default: 35.0%%)",
    )
    parser.add_argument(
        "--max-pct-from-52w-high",
        type=float,
        default=20.0,
        help="Maximum distance percentage from 52-week High (default: 20.0%%)",
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
        help="Output format: table (default), json, or symbols (plain list)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional path to write JSON results",
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

    candidates = screen_multibaggers(
        catalog_dir=catalog_dir,
        asof_date=asof_date,
        min_return_1y=args.min_return_1y,
        max_pct_from_high=args.max_pct_from_52w_high,
    )

    if args.top and args.top > 0:
        candidates = candidates[: args.top]

    # Handle output formats
    if args.format == "symbols":
        print(" ".join(c["symbol"] for c in candidates))
        return 0

    output_payload = {
        "asof_date": asof_date.isoformat(),
        "screening_rules": {
            "min_return_1y": args.min_return_1y,
            "max_pct_from_52w_high": args.max_pct_from_52w_high,
            "trend_rule": "Close >= 200 SMA and Rising 200 SMA",
        },
        "count": len(candidates),
        "candidates": candidates,
    }

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(output_payload, indent=2) + "\n", encoding="utf-8")
        console.print(f"[green]Saved {len(candidates)} candidates to {args.out}[/green]")

    if args.format == "json":
        print(json.dumps(output_payload, indent=2))
        return 0

    # Rich formatted table
    console.print()
    console.print(
        Panel(
            f"[bold cyan]Point-in-Time Multibagger Screener[/bold cyan] (Evaluated strictly as of [bold yellow]{asof_date}[/bold yellow])\n"
            f"[dim]Rules: 1-Year Return ≥ {args.min_return_1y:.1f}% | Price ≥ 200 SMA & Rising | Within {args.max_pct_from_52w_high:.1f}% of 52w High[/dim]",
            border_style="cyan",
        )
    )

    table = Table(
        title=f"Screened Multibagger Candidates as of {asof_date} ({len(candidates)} found)",
        border_style="blue",
    )
    table.add_column("Symbol", style="bold cyan")
    table.add_column("Price (₹)", justify="right")
    table.add_column("1-Yr Return (Known)", justify="right", style="green")
    table.add_column("From 52w High", justify="right", style="magenta")
    table.add_column("200 SMA", justify="right", style="dim")
    table.add_column("Trend Status", style="green")
    table.add_column("ROCE %", justify="right", style="yellow")
    table.add_column("Debt/Eq", justify="right")

    for c in candidates:
        roce_s = f"{c['roce_pct']:.1f}%" if c.get("roce_pct") is not None else "-"
        de_s = f"{c['debt_to_equity']:.2f}" if c.get("debt_to_equity") is not None else "-"
        trend_s = "Stage 2 Strong" if c["above_sma50"] else "Stage 2 Base"
        table.add_row(
            c["symbol"],
            f"₹{c['close']:,.2f}",
            f"{c['ret_1y']:+.1f}%",
            f"{c['pct_from_high']:+.1f}%",
            f"₹{c['sma200']:,.2f}",
            trend_s,
            roce_s,
            de_s,
        )

    console.print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
