"""Contrarian value and high-dividend-yield screener near 52-week low."""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Any

from honba_examples.screening.common import DEFAULT_MAX_STALE_DAYS, stale_price_sql
from honba_examples.screening.paths import find_duckdb_path

logger = logging.getLogger(__name__)


def screen_value_and_dividend(
    catalog_dir: Path,
    asof_date: dt.date,
    train_years: int = 3,
    mode: str = "all",
    max_pct_from_52w_low: float = 15.0,
    min_div_yield: float = 0.0,
    min_roce: float = 10.0,
    max_pe: float | None = None,
    max_pb: float | None = None,
    max_debt_equity: float = 1.5,
    require_positive_fcf: bool = False,
    fiscal_lag_days: int = 0,
    max_stale_days: int | None = DEFAULT_MAX_STALE_DAYS,
    top: int | None = None,
) -> list[dict[str, Any]]:
    """Screen contrarian value / high-dividend candidates near the 52-week low.

    Point-in-time (zero lookahead): prices come from the 1D Parquet catalog up to ``asof_date``;
    fundamentals come from ``fundamentals.duckdb`` restricted to fiscal periods ending on or before
    ``asof_date`` (optionally shifted back by ``fiscal_lag_days`` to model the reporting lag).
    Valuation is derived, never read from a live snapshot:

    * ``P/E`` = as-of close / latest reported EPS (only when EPS > 0).
    * ``P/B`` = as-of close / book value per share (equity / diluted average shares).
    * ``dividend yield`` = trailing annual cash dividend per share / as-of close.

    A symbol whose last bar is more than ``max_stale_days`` calendar days before ``asof_date``
    is skipped (delisted / suspended); ``None`` or ``0`` disables the check.
    """
    import duckdb

    catalog_glob = str(catalog_dir / "*" / "*.parquet")
    db_path = find_duckdb_path()
    use_fund = db_path is not None
    train_start = (asof_date - dt.timedelta(days=int(train_years * 365.25))).isoformat()
    asof_str = asof_date.isoformat()
    cutoff_str = (asof_date - dt.timedelta(days=max(0, fiscal_lag_days))).isoformat()

    con = duckdb.connect()
    if use_fund:
        con.execute(f"ATTACH '{db_path}' AS fund (READ_ONLY)")

    # Derived point-in-time valuation expressions (NULL-safe when the fundamentals DB is absent).
    pe_expr = (
        "CASE WHEN r.eps IS NOT NULL AND r.eps > 0 THEN round(p.close / r.eps, 1) ELSE NULL END"
        if use_fund
        else "CAST(NULL AS DOUBLE)"
    )
    pb_expr = (
        "CASE WHEN s.shares IS NOT NULL AND s.shares > 0 AND r.stockholders_equity IS NOT NULL "
        "THEN round(p.close / (r.stockholders_equity / s.shares), 2) ELSE NULL END"
        if use_fund
        else "CAST(NULL AS DOUBLE)"
    )
    div_expr = (
        "CASE WHEN s.shares IS NOT NULL AND s.shares > 0 "
        "THEN round(abs(coalesce(s.div_cfo, s.div_common, 0)) / s.shares / p.close * 100, 2) "
        "ELSE NULL END"
        if use_fund
        else "CAST(NULL AS DOUBLE)"
    )

    fund_ctes = (
        f"""
    , ratios AS (\n        SELECT symbol, roce_pct, debt_to_equity, operating_margin_pct,
               eps, free_cash_flow, stockholders_equity
        FROM fund.financial_ratios
        WHERE fiscal_date <= '{cutoff_str}'
          AND (period_type = 'FY' OR period_type IS NULL)
        QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY fiscal_date DESC) = 1
    ),
    stmt AS (
        SELECT symbol, fiscal_date,
            MAX(CASE WHEN line_item = 'Cash Dividends Paid' THEN value END) AS div_cfo,
            MAX(CASE WHEN line_item = 'Common Stock Dividend Paid' THEN value END) AS div_common,
            MAX(CASE WHEN line_item = 'Diluted Average Shares' THEN value END) AS shares
        FROM fund.financial_statements
        WHERE fiscal_date <= '{cutoff_str}'
          AND (period_type = 'FY' OR period_type IS NULL)
        GROUP BY symbol, fiscal_date
    ),
    stmt_latest AS (
        SELECT symbol, div_cfo, div_common, shares
        FROM stmt
        QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY fiscal_date DESC) = 1
    )
    """
        if use_fund
        else ""
    )
    fund_join = (
        """
    LEFT JOIN fund.companies c ON p.symbol = c.symbol
    LEFT JOIN ratios r ON p.symbol = r.symbol
    LEFT JOIN stmt_latest s ON p.symbol = s.symbol
    """
        if use_fund
        else ""
    )

    filters = [f"AND ((p.close / p.low52) - 1.0) * 100 <= {max_pct_from_52w_low}"]
    if use_fund:
        filters.append(f"AND (r.debt_to_equity IS NULL OR r.debt_to_equity <= {max_debt_equity})")
        filters.append(f"AND (r.roce_pct IS NULL OR r.roce_pct >= {min_roce})")
        if min_div_yield > 0:
            filters.append(f"AND {div_expr} IS NOT NULL AND {div_expr} >= {min_div_yield}")
        if max_pe is not None:
            filters.append(f"AND {pe_expr} IS NOT NULL AND {pe_expr} <= {max_pe}")
        if max_pb is not None:
            filters.append(f"AND {pb_expr} IS NOT NULL AND {pb_expr} <= {max_pb}")
        if require_positive_fcf:
            filters.append("AND r.free_cash_flow IS NOT NULL AND r.free_cash_flow > 0")
    filter_sql = "\n      ".join(filters)

    order_sql = (
        "div_yield_pct DESC NULLS LAST, pct_from_low ASC"
        if mode == "dividend"
        else "pct_from_low ASC, pb_ratio ASC NULLS LAST, pe_ratio ASC NULLS LAST"
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
            symbol, bar_date, close,
            MIN(low) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) as low52,
            FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) as first_train_close,
            ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY bar_date DESC) as rn,
            COUNT(*) OVER (PARTITION BY symbol ORDER BY bar_date) as bar_count
        FROM bars
    ),
    recent_touch AS (
        SELECT symbol, MIN(low) AS recent_low
        FROM bars
        WHERE bar_date >= ('{asof_str}'::DATE - INTERVAL 30 DAY)
        GROUP BY symbol
    ){fund_ctes}
    SELECT 
        p.symbol,
        {"coalesce(c.company_name, p.symbol)" if use_fund else "p.symbol"} as company_name,
        round(p.close, 2) as asof_close,
        round(p.low52, 2) as low52,
        round(((p.close / p.low52) - 1.0) * 100, 1) as pct_from_low,
        round(((p.close / p.first_train_close) - 1.0) * 100, 1) as train_ret_pct,
        coalesce(t.recent_low <= p.low52 * 1.02, false) as hit_52w_low_recently,
        {div_expr} as div_yield_pct,
        {pe_expr} as pe_ratio,
        {pb_expr} as pb_ratio,
        {"round(r.roce_pct, 1)" if use_fund else "NULL"} as roce_pct,
        {"round(r.debt_to_equity, 2)" if use_fund else "NULL"} as debt_to_equity,
        {"round(r.operating_margin_pct, 1)" if use_fund else "NULL"} as operating_margin_pct,
        {"round(r.free_cash_flow, 0)" if use_fund else "NULL"} as free_cash_flow
    FROM prices p
    LEFT JOIN recent_touch t ON p.symbol = t.symbol
    {fund_join}
    WHERE p.rn = 1 AND p.bar_count >= 252
      {stale_price_sql(asof_date, max_stale_days, "p.bar_date")}
      {filter_sql}
    ORDER BY {order_sql}
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
                "low52": float(r[3]),
                "pct_from_low": float(r[4]),
                "train_ret_pct": float(r[5]),
                "hit_52w_low_recently": bool(r[6]),
                "div_yield_pct": float(r[7]) if r[7] is not None else 0.0,
                "pe_ratio": float(r[8]) if r[8] is not None else None,
                "pb_ratio": float(r[9]) if r[9] is not None else None,
                "roce_pct": float(r[10]) if r[10] is not None else None,
                "debt_to_equity": float(r[11]) if r[11] is not None else None,
                "operating_margin_pct": float(r[12]) if r[12] is not None else None,
                "free_cash_flow": float(r[13]) if r[13] is not None else None,
            }
        )
    return candidates
