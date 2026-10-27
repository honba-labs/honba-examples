"""Prospective multibagger (Stage 2 momentum) screener."""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Any

from honba_examples.screening.common import (
    DEFAULT_FISCAL_LAG_DAYS,
    DEFAULT_MAX_STALE_DAYS,
    fiscal_cutoff,
    stale_price_sql,
)
from honba_examples.screening.paths import find_duckdb_path

logger = logging.getLogger(__name__)


def screen_multibaggers(
    catalog_dir: Path,
    asof_date: dt.date,
    train_years: int = 3,
    min_return_1y: float = 15.0,
    max_return_1y: float | None = None,
    max_pct_from_high: float = 15.0,
    max_debt_to_equity: float | None = 1.5,
    min_roce: float | None = None,
    fiscal_lag_days: int = DEFAULT_FISCAL_LAG_DAYS,
    max_stale_days: int | None = DEFAULT_MAX_STALE_DAYS,
    allow_missing: bool = False,
    top: int | None = None,
) -> list[dict[str, Any]]:
    """Screen prospective 'going-to-be' multibaggers strictly as of asof_date.

    Identifies Stage 2 breakout momentum leaders before late-stage exhaustion:
    1. Trend template: Price >= SMA50 >= SMA200, with rising 200 SMA.
    2. Consolidation base: Price within 15% of 52-week high.
    3. Momentum acceleration: Positive 3-month momentum (> 0%).
    4. Volume accumulation: Recent 20-day volume expansion over 200-day baseline.
    5. Fundamental discipline: ROCE capital efficiency and debt coverage, from the latest FY row
       whose period end is at least ``fiscal_lag_days`` before ``asof_date`` (a lag proxy, not a
       filing date: the tables have none).

    A symbol whose last bar is more than ``max_stale_days`` calendar days before ``asof_date``
    is skipped (delisted / suspended); ``None`` or ``0`` disables the check. A missing
    debt-to-equity (or no fundamentals row) fails the leverage gate unless ``allow_missing=True``;
    a missing ROCE adds 0 points to ``mb_score``. When ``min_roce`` is set, ROCE becomes a hard
    gate (symbols below the floor, or with missing ROCE unless ``allow_missing``, are excluded)
    and the fundamentals lakehouse is required.
    """
    import duckdb

    catalog_glob = str(catalog_dir / "**" / "*.parquet")
    db_path = find_duckdb_path()
    train_start = (asof_date - dt.timedelta(days=int(train_years * 365.25))).isoformat()
    asof_str = asof_date.isoformat()
    cutoff_str = fiscal_cutoff(asof_date, fiscal_lag_days)

    con = duckdb.connect()
    if db_path:
        con.execute(f"ATTACH '{db_path}' AS fund (READ_ONLY)")

    fund_join = (
        f"""
        LEFT JOIN fund.companies c ON m.symbol = c.symbol
        LEFT JOIN (
            SELECT symbol, roce_pct, debt_to_equity
            FROM fund.financial_ratios
            WHERE fiscal_date <= '{cutoff_str}'
              AND (period_type = 'FY' OR period_type IS NULL)
            QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY fiscal_date DESC) = 1
        ) r ON m.symbol = r.symbol
        """
        if db_path
        else ""
    )

    max_ret_filter = (
        f"AND ((m.close / m.close_1y) - 1.0) * 100 <= {max_return_1y}"
        if max_return_1y is not None
        else ""
    )
    if min_roce is not None and not db_path:
        # ROCE cannot be verified without the fundamentals lakehouse: no symbol can
        # clear the floor, so the screen is empty (it never silently ignores the gate).
        return []
    # "missing debt/equity fails unless allow_missing" must be defined before the
    # debt_gate below references it (same block, kept adjacent for clarity).
    null_de_sql = "r.debt_to_equity IS NULL OR " if allow_missing else ""
    roce_gate = (
        f"AND ({'r.roce_pct IS NULL OR ' if allow_missing else ''}r.roce_pct >= {min_roce})"
        if min_roce is not None
        else ""
    )
    debt_gate = (
        f"AND ({null_de_sql}r.debt_to_equity <= {max_debt_to_equity})"
        if db_path and max_debt_to_equity is not None
        else ""
    )

    # DuckDB's least() ignores NULLs, so a missing ROCE must be mapped to 0 points explicitly.
    roce_points_sql = (
        "CASE WHEN r.roce_pct IS NULL THEN 0.0 ELSE least(r.roce_pct, 20.0) * 0.5 END"
        if db_path
        else "7.5"
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
    prices AS (
        SELECT 
            symbol, bar_date, close, high, low, volume,
            AVG(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) as sma50,
            AVG(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) as sma200,
            AVG(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 219 PRECEDING AND 20 PRECEDING) as sma200_prior,
            MAX(high) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) as high52,
            FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) as first_train_close,
            NTH_VALUE(close, 252) OVER (PARTITION BY symbol ORDER BY bar_date DESC ROWS BETWEEN CURRENT ROW AND 251 FOLLOWING) as close_1y,
            NTH_VALUE(close, 63) OVER (PARTITION BY symbol ORDER BY bar_date DESC ROWS BETWEEN CURRENT ROW AND 62 FOLLOWING) as close_3m,
            AVG(volume) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as vol20,
            AVG(volume) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) as vol200,
            ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY bar_date DESC) as rn,
            COUNT(*) OVER (PARTITION BY symbol ORDER BY bar_date) as bar_count
        FROM bars
    ),
    metrics AS (
        SELECT 
            symbol, bar_date, close, sma50, sma200, sma200_prior, high52, first_train_close,
            coalesce(close_1y, first_train_close) as close_1y,
            coalesce(close_3m, close) as close_3m,
            vol20, vol200, rn, bar_count
        FROM prices
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
        (m.sma200 >= m.sma200_prior) as sma200_rising,
        round(m.vol20 / nullif(m.vol200, 0), 2) as vol_surge,
        {"round(r.roce_pct, 1)" if db_path else "NULL"} as roce_pct,
        {"round(r.debt_to_equity, 2)" if db_path else "NULL"} as debt_to_equity,
        round(
            least(greatest(((m.close / m.close_1y) - 1.0) * 100, 0.0), 100.0) * 0.35 +
            least(greatest(((m.close / m.close_3m) - 1.0) * 100, 0.0), 50.0) * 0.25 +
            (100.0 + greatest(((m.close / m.high52) - 1.0) * 100, -50.0)) * 0.15 +
            least(m.vol20 / nullif(m.vol200, 0) * 10.0, 20.0) +
            {roce_points_sql},
            1
        ) as mb_score
    FROM metrics m
    {fund_join}
    WHERE m.rn = 1 AND m.bar_count >= 252
      {stale_price_sql(asof_date, max_stale_days, "m.bar_date")}
      AND m.close >= m.sma50
      AND m.sma50 >= m.sma200
      AND m.sma200 >= m.sma200_prior
      AND m.close >= m.close_3m
      AND ((m.close / m.close_1y) - 1.0) * 100 >= {min_return_1y}
      AND ((m.close / m.high52) - 1.0) * 100 >= -{abs(max_pct_from_high)}
      {max_ret_filter}
      {roce_gate}
      {debt_gate}
    ORDER BY mb_score DESC, ret_3m DESC, vol_surge DESC
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
