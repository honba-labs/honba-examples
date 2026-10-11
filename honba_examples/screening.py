"""honba_examples.screening: Generic Point-in-Time Screener Engine.

Provides zero-lookahead screening primitives powered by DuckDB SQL:
- `screen_multibaggers`: Prospective "going-to-be" multibagger screener identifying early Stage 2
  breakouts, unexhausted consolidation bases, volume surges, and capital efficiency prior to --asof.
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
    """Determine the latest trading session date quickly without full catalog scans."""
    try:
        import duckdb

        # Check prominent liquid stock directories first for instant date resolution
        check_symbols = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN"]
        for sym in check_symbols:
            sym_dir = catalog_dir / sym
            if sym_dir.is_dir():
                parquets = sorted(sym_dir.glob("*.parquet"))
                if parquets:
                    res = duckdb.execute(
                        f"SELECT max(epoch_ms(ts // 1000000)::DATE) FROM read_parquet('{parquets[-1]}')"
                    ).fetchone()
                    if res and res[0]:
                        return res[0]

        # Fallback: inspect any single parquet file
        any_parquet = next(catalog_dir.glob("*/*.parquet"), None)
        if any_parquet:
            res = duckdb.execute(
                f"SELECT max(epoch_ms(ts // 1000000)::DATE) FROM read_parquet('{any_parquet}')"
            ).fetchone()
            if res and res[0]:
                return res[0]
    except Exception as exc:  # noqa: BLE001
        logger.debug("Falling back on system date: %s", exc)

    return dt.datetime.now(dt.timezone.utc).date()


def screen_multibaggers(
    catalog_dir: Path,
    asof_date: dt.date,
    train_years: int = 3,
    min_return_1y: float = 15.0,
    max_return_1y: float = 95.0,
    max_pct_from_high: float = 15.0,
    max_debt_to_equity: float = 1.5,
    top: int | None = None,
) -> list[dict[str, Any]]:
    """Screen prospective 'going-to-be' multibaggers strictly as of asof_date.

    Identifies early Stage 2 breakout setups before parabolic exhaustion:
    1. Emerging momentum sweet-spot: 1-yr return between min_return_1y (15%) and max_return_1y (95%).
       (Excludes stocks that already ran 300%+ and entered late-stage distribution).
    2. Consolidation base & breakout: price within 15% of 52-week high after base building.
    3. Stage 2 trend: price above 200 SMA, 200 SMA rising.
    4. Fresh momentum ignition: positive 3-month momentum.
    5. Quality overlay: manageable debt (Debt/Equity <= 1.5) and high capital efficiency (ROCE).
    """
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
            close, high, low, volume
        FROM read_parquet('{catalog_glob}', filename=true)
        WHERE bar_date >= '{train_start}' AND bar_date <= '{asof_str}'
    ),
    metrics AS (
        SELECT 
            symbol, bar_date, close, volume,
            AVG(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) as sma200,
            AVG(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 249 PRECEDING AND 50 PRECEDING) as sma200_prior,
            MAX(high)  OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) as high52,
            FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) as close_1y,
            FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 62 PRECEDING AND CURRENT ROW)  as close_3m,
            FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) as first_train_close,
            AVG(volume) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)  as vol20,
            AVG(volume) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) as vol200,
            ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY bar_date DESC) as rn,
            COUNT(*) OVER (PARTITION BY symbol ORDER BY bar_date) as bar_count
        FROM bars
    )
    SELECT 
        m.symbol,
        {"coalesce(c.company_name, m.symbol)" if db_path else "m.symbol"} as company_name,
        round(m.close, 2) as asof_close,
        round(((m.close / m.close_1y) - 1.0) * 100, 1) as ret_1y,
        round(((m.close / m.close_3m) - 1.0) * 100, 1) as ret_3m,
        round(((m.close / m.first_train_close) - 1.0) * 100, 1) as train_ret_pct,
        round(((m.close / m.high52) - 1.0) * 100, 1) as pct_from_high,
        round(m.sma200, 2) as sma200,
        m.sma200 >= m.sma200_prior as sma200_rising,
        round(m.vol20 / nullif(m.vol200, 0), 2) as vol_surge,
        {"round(r.roce_pct, 1)" if db_path else "NULL"} as roce_pct,
        {"round(r.debt_to_equity, 2)" if db_path else "NULL"} as debt_to_equity,
        -- Prospective Multibagger Score: Early breakout tightness, volume accumulation, momentum sweet spot, capital efficiency
        round(
            (100.0 + round(((m.close / m.high52) - 1.0) * 100, 1)) * 0.4 +
            (CASE WHEN (m.close / m.close_1y) BETWEEN 1.15 AND 1.85 THEN 30.0 ELSE 10.0 END) +
            (CASE WHEN m.vol20 > m.vol200 THEN 15.0 ELSE 5.0 END) +
            coalesce(least({"r.roce_pct" if db_path else "15.0"}, 25.0), 10.0),
            1
        ) as mb_score
    FROM metrics m
    {fund_join}
    WHERE m.rn = 1 AND m.bar_count >= 252
      AND m.close >= m.sma200
      AND m.sma200 >= m.sma200_prior
      -- Emerging Stage 2 momentum sweet spot (prospective runners, not exhausted tops)
      AND ((m.close / m.close_1y) - 1.0) * 100 BETWEEN {min_return_1y} AND {max_return_1y}
      AND ((m.close / m.high52) - 1.0) * 100 >= -{abs(max_pct_from_high)}
      AND ({"r.debt_to_equity IS NULL OR r.debt_to_equity <= " + str(max_debt_to_equity) if db_path else "1=1"})
    ORDER BY mb_score DESC, ret_3m DESC
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
                "ret_3m": float(r[4]),
                "train_ret_pct": float(r[5]),
                "pct_from_high": float(r[6]),
                "sma200": float(r[7]),
                "sma200_rising": bool(r[8]),
                "vol_surge": float(r[9]) if r[9] is not None else 1.0,
                "roce_pct": float(r[10]) if r[10] is not None else None,
                "debt_to_equity": float(r[11]) if r[11] is not None else None,
                "mb_score": float(r[12]),
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
