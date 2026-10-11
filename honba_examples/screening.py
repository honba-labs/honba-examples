"""honba_examples.screening: Generic Point-in-Time Screener Engine.

Provides zero-lookahead screening primitives powered by DuckDB SQL:
- `screen_multibaggers`: Institutional Stage 2 momentum screener using 3-5 years training data.
- `screen_value_and_dividend`: Contrarian value & high dividend yield screener near 52-week low.
- Point-in-time price and DuckDB fundamentals loaders.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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
    """Optionally locate fundamentals.duckdb for fundamental ratio overlay."""
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
    """Determine the latest trading session date available in the catalog via DuckDB."""
    try:
        import duckdb

        catalog_glob = str(catalog_dir / "*" / "*.parquet")
        res = duckdb.execute(
            f"SELECT max(epoch_ms(ts // 1000000)::DATE) FROM read_parquet('{catalog_glob}')"
        ).fetchone()
        if res and res[0]:
            return res[0]
    except Exception as exc:  # noqa: BLE001
        logger.debug("Falling back on filesystem timestamp check: %s", exc)

    return dt.datetime.now(dt.timezone.utc).date()


def screen_multibaggers(
    catalog_dir: Path,
    asof_date: dt.date,
    train_years: int = 3,
    min_return_1y: float = 35.0,
    max_pct_from_high: float = 20.0,
    top: int | None = None,
) -> list[dict[str, Any]]:
    """Screen candidate multibaggers strictly as of asof_date using train_years historical bars."""
    import duckdb

    catalog_glob = str(catalog_dir / "*" / "*.parquet")
    db_path = find_duckdb_path()
    train_start = (asof_date - dt.timedelta(days=int(train_years * 365.25))).isoformat()
    asof_str = asof_date.isoformat()

    con = duckdb.connect()
    if db_path:
        con.execute(f"ATTACH '{db_path}' AS fund (READ_ONLY)")

    fund_join = (
        f"""
        LEFT JOIN fund.companies c ON m.symbol = c.symbol
        LEFT JOIN (
            SELECT symbol, roce_pct, debt_to_equity
            FROM fund.financial_ratios
            WHERE fiscal_date <= '{asof_str}'
              AND (period_type = 'FY' OR period_type IS NULL)
            QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY fiscal_date DESC) = 1
        ) r ON m.symbol = r.symbol
        """
        if db_path
        else ""
    )

    query = f"""
    WITH bars AS (
        SELECT 
            regexp_extract(filename, 'NSE/([^/]+)/', 1) as symbol,
            epoch_ms(ts // 1000000)::DATE as bar_date,
            close, high, low
        FROM read_parquet('{catalog_glob}', filename=true)
        WHERE bar_date >= '{train_start}' AND bar_date <= '{asof_str}'
    ),
    metrics AS (
        SELECT 
            symbol, bar_date, close,
            AVG(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) as sma200,
            AVG(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 249 PRECEDING AND 50 PRECEDING) as sma200_prior,
            MAX(high)  OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) as high52,
            FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) as close_1y,
            FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) as first_train_close,
            ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY bar_date DESC) as rn,
            COUNT(*) OVER (PARTITION BY symbol ORDER BY bar_date) as bar_count
        FROM bars
    )
    SELECT 
        m.symbol,
        {"coalesce(c.company_name, m.symbol)" if db_path else "m.symbol"} as company_name,
        round(m.close, 2) as asof_close,
        round(((m.close / m.close_1y) - 1.0) * 100, 1) as ret_1y,
        round(((m.close / m.first_train_close) - 1.0) * 100, 1) as train_ret_pct,
        round(((m.close / m.high52) - 1.0) * 100, 1) as pct_from_high,
        round(m.sma200, 2) as sma200,
        m.sma200 >= m.sma200_prior as sma200_rising,
        {"round(r.roce_pct, 1)" if db_path else "NULL"} as roce_pct,
        {"round(r.debt_to_equity, 2)" if db_path else "NULL"} as debt_to_equity
    FROM metrics m
    {fund_join}
    WHERE m.rn = 1 AND m.bar_count >= 252
      AND m.close >= m.sma200
      AND m.sma200 >= m.sma200_prior
      AND (m.close / m.close_1y) >= {1.0 + (min_return_1y / 100.0)}
      AND (m.close / m.high52) >= {1.0 - (abs(max_pct_from_high) / 100.0)}
    ORDER BY ret_1y DESC
    {f"LIMIT {top}" if top and top > 0 else ""}
    """

    rows = con.execute(query).fetchall()
    candidates: list[dict[str, Any]] = []
    for r in rows:
        candidates.append(
            {
                "symbol": r[0],
                "company_name": r[1],
                "asof_date": asof_str,
                "close": float(r[2]),
                "ret_1y": float(r[3]),
                "train_ret_pct": float(r[4]),
                "pct_from_high": float(r[5]),
                "sma200": float(r[6]),
                "sma200_rising": bool(r[7]),
                "roce_pct": float(r[8]) if r[8] is not None else None,
                "debt_to_equity": float(r[9]) if r[9] is not None else None,
            }
        )
    return candidates


def screen_value_and_dividend(
    catalog_dir: Path,
    asof_date: dt.date,
    train_years: int = 3,
    mode: str = "all",
    max_pct_from_52w_low: float = 15.0,
    min_div_yield: float = 0.0,
    min_roce: float = 10.0,
    max_pe: float | None = None,
    max_debt_equity: float = 1.5,
    top: int | None = None,
) -> list[dict[str, Any]]:
    """Screen contrarian value and dividend yield candidates near 52-week low."""
    import duckdb

    catalog_glob = str(catalog_dir / "*" / "*.parquet")
    db_path = find_duckdb_path()
    train_start = (asof_date - dt.timedelta(days=int(train_years * 365.25))).isoformat()
    asof_str = asof_date.isoformat()

    con = duckdb.connect()
    if db_path:
        con.execute(f"ATTACH '{db_path}' AS fund (READ_ONLY)")

    fund_join = (
        f"""
        LEFT JOIN fund.companies c ON p.symbol = c.symbol
        LEFT JOIN (
            SELECT symbol, abs(value) as div_paid
            FROM fund.financial_statements
            WHERE line_item IN ('Cash Dividends Paid', 'Common Stock Dividend Paid')
              AND fiscal_date <= '{asof_str}'
              AND (period_type = 'FY' OR period_type IS NULL)
            QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY fiscal_date DESC) = 1
        ) d ON p.symbol = d.symbol
        LEFT JOIN (
            SELECT symbol, roce_pct, debt_to_equity
            FROM fund.financial_ratios
            WHERE fiscal_date <= '{asof_str}'
              AND (period_type = 'FY' OR period_type IS NULL)
            QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY fiscal_date DESC) = 1
        ) r ON p.symbol = r.symbol
        """
        if db_path
        else ""
    )

    query = f"""
    WITH bars AS (
        SELECT 
            regexp_extract(filename, 'NSE/([^/]+)/', 1) as symbol,
            epoch_ms(ts // 1000000)::DATE as bar_date,
            close, high, low
        FROM read_parquet('{catalog_glob}', filename=true)
        WHERE bar_date >= '{train_start}' AND bar_date <= '{asof_str}'
    ),
    prices AS (
        SELECT 
            symbol, bar_date, close,
            MIN(low) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) as low52,
            MIN(low) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)  as recent_low,
            FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) as first_train_close,
            ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY bar_date DESC) as rn,
            COUNT(*) OVER (PARTITION BY symbol ORDER BY bar_date) as bar_count
        FROM bars
    )
    SELECT 
        p.symbol,
        {"coalesce(c.company_name, p.symbol)" if db_path else "p.symbol"} as company_name,
        round(p.close, 2) as asof_close,
        round(p.low52, 2) as low52,
        round(((p.close / p.low52) - 1.0) * 100, 1) as pct_from_low,
        round(((p.close / p.first_train_close) - 1.0) * 100, 1) as train_ret_pct,
        (p.recent_low <= p.low52 * 1.01) as hit_52w_low_recently,
        {"round((d.div_paid / c.market_cap) * 100, 2)" if db_path else "0.0"} as div_yield_pct,
        {"round(c.pe_trailing, 1)" if db_path else "NULL"} as pe_ratio,
        {"round(c.pb_ratio, 2)" if db_path else "NULL"} as pb_ratio,
        {"round(r.roce_pct, 1)" if db_path else "NULL"} as roce_pct,
        {"round(r.debt_to_equity, 2)" if db_path else "NULL"} as debt_to_equity
    FROM prices p
    {fund_join}
    WHERE p.rn = 1 AND p.bar_count >= 252
      AND ((p.close / p.low52) - 1.0) * 100 <= {max_pct_from_52w_low}
    ORDER BY pct_from_low ASC
    {f"LIMIT {top}" if top and top > 0 else ""}
    """

    rows = con.execute(query).fetchall()
    candidates: list[dict[str, Any]] = []
    for r in rows:
        div_y = float(r[7]) if r[7] is not None else 0.0
        roce = float(r[10]) if r[10] is not None else None
        de = float(r[11]) if r[11] is not None else None
        pe = float(r[8]) if r[8] is not None else None

        if de is not None and de > max_debt_equity:
            continue
        if roce is not None and roce < min_roce:
            continue
        if max_pe is not None and pe is not None and (pe <= 0 or pe > max_pe):
            continue

        if mode == "dividend":
            effective_min = min_div_yield if min_div_yield > 0.0 else 2.5
            if div_y < effective_min:
                continue
        elif mode == "value":
            if pe is not None and pe > 25.0:
                continue
        else:
            if min_div_yield > 0.0 and div_y < min_div_yield:
                continue

        candidates.append(
            {
                "symbol": r[0],
                "company_name": r[1],
                "asof_date": asof_str,
                "close": float(r[2]),
                "low52": float(r[3]),
                "pct_from_low": float(r[4]),
                "train_ret_pct": float(r[5]),
                "hit_52w_low_recently": bool(r[6]),
                "div_yield_pct": div_y,
                "pe_ratio": pe,
                "pb_ratio": float(r[9]) if r[9] is not None else None,
                "roce_pct": roce,
                "debt_to_equity": de,
            }
        )

    if mode == "dividend":
        candidates.sort(key=lambda x: (x["div_yield_pct"], -x["pct_from_low"]), reverse=True)
    else:
        candidates.sort(key=lambda x: (x["pct_from_low"], -(x["div_yield_pct"] or 0.0)))

    if top and top > 0:
        candidates = candidates[:top]
    return candidates
