"""Benjamin Graham screeners: Intelligent Investor and Net-Net (NCAV)."""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Any

from honba_examples.screening.paths import find_duckdb_path

logger = logging.getLogger(__name__)


def screen_intelligent_investor(
    catalog_dir: Path,
    asof_date: dt.date,
    train_years: int = 3,
    mode: str = "defensive",
    min_margin_of_safety: float = 0.0,
    max_graham_product: float = 22.5,
    max_pe: float | None = 20.0,
    max_pb: float | None = 2.0,
    max_debt_equity: float | None = 1.0,
    min_current_ratio: float = 1.2,
    min_div_yield: float = 0.0,
    require_positive_fcf: bool = False,
    fiscal_lag_days: int = 0,
    top: int | None = None,
) -> list[dict[str, Any]]:
    """Screen value candidates based on Benjamin Graham's *The Intelligent Investor*.

    Evaluates Graham's core principles:
    1. Valuation Discipline: P/E <= 15-20, P/B <= 1.5-2.0, and Graham Multiplier (P/E * P/B) <= 22.5.
    2. Intrinsic Graham Value: sqrt(22.5 * EPS * BVPS).
    3. Margin of Safety: Percentage discount of market price relative to the Graham Value.
    4. Financial Solvency: Low Debt-to-Equity and robust Current Ratio (Current Assets / Current Liabilities).
    5. Earnings & Dividend Foundation: Positive audited EPS and shareholder equity.

    Modes:
    - ``defensive``: Stricter Graham criteria (P/E * P/B <= 22.5, D/E <= 0.6, positive dividend).
    - ``enterprising``: Relaxed multiples allowing turnaround opportunities with large margin of safety.
    """
    import duckdb

    catalog_glob = str(catalog_dir / "*" / "*.parquet")
    db_path = find_duckdb_path()
    if not db_path:
        raise FileNotFoundError(
            "Benjamin Graham screening requires fundamentals.duckdb for balance sheet & income metrics."
        )

    train_start = (asof_date - dt.timedelta(days=int(train_years * 365.25))).isoformat()
    asof_str = asof_date.isoformat()
    cutoff_str = (asof_date - dt.timedelta(days=max(0, fiscal_lag_days))).isoformat()

    con = duckdb.connect()
    con.execute(f"ATTACH '{db_path}' AS fund (READ_ONLY)")

    # Adjust default thresholds for mode
    effective_max_product = 22.5 if mode == "defensive" else max_graham_product
    effective_max_debt_eq = 0.6 if mode == "defensive" else max_debt_equity
    effective_max_pe = 15.0 if mode == "defensive" else max_pe
    effective_max_pb = 1.5 if mode == "defensive" else max_pb

    filters = [
        "r.eps IS NOT NULL AND r.eps > 0",
        "r.stockholders_equity IS NOT NULL AND r.stockholders_equity > 0",
        "s.shares IS NOT NULL AND s.shares > 0",
    ]

    # Graham Number and Margin of Safety expression:
    # graham_val = sqrt(22.5 * r.eps * (r.stockholders_equity / s.shares))
    # mos_pct = ((graham_val - p.close) / graham_val) * 100
    filters.append(
        f"((sqrt(22.5 * r.eps * (r.stockholders_equity / s.shares)) - p.close) / "
        f"nullif(sqrt(22.5 * r.eps * (r.stockholders_equity / s.shares)), 0)) * 100 >= {min_margin_of_safety}"
    )

    if effective_max_product and effective_max_product > 0:
        filters.append(
            f"(p.close / nullif(r.eps, 0)) * (p.close / nullif(r.stockholders_equity / nullif(s.shares, 0), 0)) <= {effective_max_product}"
        )

    if effective_max_pe and effective_max_pe > 0:
        filters.append(f"(p.close / nullif(r.eps, 0)) <= {effective_max_pe}")

    if effective_max_pb and effective_max_pb > 0:
        filters.append(
            f"(p.close / nullif(r.stockholders_equity / nullif(s.shares, 0), 0)) <= {effective_max_pb}"
        )

    if effective_max_debt_eq and effective_max_debt_eq > 0:
        filters.append(f"(r.debt_to_equity IS NULL OR r.debt_to_equity <= {effective_max_debt_eq})")

    if min_current_ratio and min_current_ratio > 0:
        filters.append(
            f"(s.current_assets IS NULL OR s.current_liabilities IS NULL OR "
            f"(s.current_assets / nullif(s.current_liabilities, 0)) >= {min_current_ratio})"
        )

    if min_div_yield > 0 or mode == "defensive":
        threshold_div = min_div_yield if min_div_yield > 0 else 0.5
        filters.append(
            f"(abs(coalesce(s.div_cfo, s.div_common, 0)) / nullif(s.shares, 0) / nullif(p.close, 0) * 100) >= {threshold_div}"
        )

    if require_positive_fcf:
        filters.append("r.free_cash_flow IS NOT NULL AND r.free_cash_flow > 0")

    filter_sql = "\n      AND ".join(filters)

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
            MAX(high) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) as high52,
            FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) as first_train_close,
            ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY bar_date DESC) as rn,
            COUNT(*) OVER (PARTITION BY symbol ORDER BY bar_date) as bar_count
        FROM bars
    ),
    ratios AS (
        SELECT symbol, roce_pct, debt_to_equity, operating_margin_pct,
               eps, free_cash_flow, stockholders_equity, net_income
        FROM fund.financial_ratios
        WHERE fiscal_date <= '{cutoff_str}'
          AND (period_type = 'FY' OR period_type IS NULL)
        QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY fiscal_date DESC) = 1
    ),
    stmt AS (
        SELECT symbol, fiscal_date,
            MAX(CASE WHEN line_item = 'Current Assets' THEN value END) AS current_assets,
            MAX(CASE WHEN line_item = 'Current Liabilities' THEN value END) AS current_liabilities,
            MAX(CASE WHEN line_item = 'Cash Dividends Paid' THEN value END) AS div_cfo,
            MAX(CASE WHEN line_item = 'Common Stock Dividend Paid' THEN value END) AS div_common,
            MAX(CASE WHEN line_item = 'Diluted Average Shares' THEN value END) AS shares
        FROM fund.financial_statements
        WHERE fiscal_date <= '{cutoff_str}'
          AND (period_type = 'FY' OR period_type IS NULL)
        GROUP BY symbol, fiscal_date
    ),
    stmt_latest AS (
        SELECT symbol, current_assets, current_liabilities, div_cfo, div_common, shares
        FROM stmt
        QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY fiscal_date DESC) = 1
    )
    SELECT 
        p.symbol,
        coalesce(c.company_name, p.symbol) as company_name,
        round(p.close, 2) as asof_close,
        round(p.low52, 2) as low52,
        round(p.high52, 2) as high52,
        round(((p.close / p.low52) - 1.0) * 100, 1) as pct_from_low,
        round(((p.close / p.high52) - 1.0) * 100, 1) as pct_from_high,
        round(((p.close / p.first_train_close) - 1.0) * 100, 1) as train_ret_pct,
        round(r.eps, 2) as eps,
        round(r.stockholders_equity / nullif(s.shares, 0), 2) as bvps,
        round(p.close / nullif(r.eps, 0), 1) as pe_ratio,
        round(p.close / nullif(r.stockholders_equity / nullif(s.shares, 0), 0), 2) as pb_ratio,
        round((p.close / nullif(r.eps, 0)) * (p.close / nullif(r.stockholders_equity / nullif(s.shares, 0), 0)), 1) as graham_product,
        round(sqrt(22.5 * r.eps * (r.stockholders_equity / nullif(s.shares, 0))), 2) as graham_num,
        round(((sqrt(22.5 * r.eps * (r.stockholders_equity / nullif(s.shares, 0))) - p.close) / 
               nullif(sqrt(22.5 * r.eps * (r.stockholders_equity / nullif(s.shares, 0))), 0)) * 100, 1) as margin_of_safety_pct,
        round(s.current_assets / nullif(s.current_liabilities, 0), 2) as current_ratio,
        round(r.debt_to_equity, 2) as debt_to_equity,
        round(abs(coalesce(s.div_cfo, s.div_common, 0)) / nullif(s.shares, 0) / nullif(p.close, 0) * 100, 2) as div_yield_pct,
        round(r.roce_pct, 1) as roce_pct
    FROM prices p
    LEFT JOIN fund.companies c ON p.symbol = c.symbol
    JOIN ratios r ON p.symbol = r.symbol
    JOIN stmt_latest s ON p.symbol = s.symbol
    WHERE p.rn = 1 AND p.bar_count >= 100
      AND {filter_sql}
    ORDER BY margin_of_safety_pct DESC, graham_product ASC, pe_ratio ASC
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
                "high52": float(r[4]),
                "pct_from_low": float(r[5]),
                "pct_from_high": float(r[6]),
                "train_ret_pct": float(r[7]),
                "eps": float(r[8]),
                "bvps": float(r[9]),
                "pe_ratio": float(r[10]),
                "pb_ratio": float(r[11]),
                "graham_product": float(r[12]),
                "graham_num": float(r[13]),
                "margin_of_safety_pct": float(r[14]),
                "current_ratio": float(r[15]) if r[15] is not None else None,
                "debt_to_equity": float(r[16]) if r[16] is not None else None,
                "div_yield_pct": float(r[17]) if r[17] is not None else 0.0,
                "roce_pct": float(r[18]) if r[18] is not None else None,
            }
        )
    return candidates


def screen_net_net(
    catalog_dir: Path,
    asof_date: dt.date,
    train_years: int = 3,
    max_price_to_ncav: float | None = None,
    min_ncav_margin_of_safety: float | None = None,
    require_positive_ncav: bool = True,
    require_positive_cfo: bool = False,
    require_positive_eps: bool = False,
    max_debt_equity: float | None = 1.0,
    min_current_ratio: float = 1.0,
    fiscal_lag_days: int = 0,
    top: int | None = None,
) -> list[dict[str, Any]]:
    """Screen deep value Net-Net (Net Current Asset Value / NCAV) stocks based on Benjamin Graham.

    Graham's NCAV Formula:
        NCAV = Current Assets - Total Liabilities
        NCAV per share = NCAV / Diluted Shares
        Classic 2/3 Net-Net: Price <= 0.67 * NCAV per share (MoS >= 33.3%)
        Full Net-Net Bargain: Price <= 1.0 * NCAV per share

    Provides downside liquidation asset buffer:
    - Current Assets (cash, receivables, inventory) exceed ALL liabilities (short + long-term debt).
    - Fixed assets, real estate, brand, plant & machinery are acquired for free.
    """
    import duckdb

    catalog_glob = str(catalog_dir / "*" / "*.parquet")
    db_path = find_duckdb_path()
    if not db_path:
        raise FileNotFoundError(
            "Net-Net screening requires fundamentals.duckdb for balance sheet current assets & liabilities."
        )

    train_start = (asof_date - dt.timedelta(days=int(train_years * 365.25))).isoformat()
    asof_str = asof_date.isoformat()
    cutoff_str = (asof_date - dt.timedelta(days=max(0, fiscal_lag_days))).isoformat()

    con = duckdb.connect()
    con.execute(f"ATTACH '{db_path}' AS fund (READ_ONLY)")

    filters = [
        "s.shares IS NOT NULL AND s.shares > 0",
        "s.current_assets IS NOT NULL",
    ]

    ncav_expr = "(s.current_assets - coalesce(s.total_liabilities, s.current_liabilities + coalesce(s.non_current_liabilities, 0)))"
    ncav_ps_expr = f"({ncav_expr} / nullif(s.shares, 0))"
    p_to_ncav_expr = f"(p.close / nullif({ncav_ps_expr}, 0))"
    mos_expr = f"((({ncav_ps_expr} - p.close) / nullif({ncav_ps_expr}, 0)) * 100)"

    if require_positive_ncav:
        filters.append(f"{ncav_expr} > 0")

    if max_price_to_ncav is not None and max_price_to_ncav > 0:
        filters.append(f"{p_to_ncav_expr} <= {max_price_to_ncav}")

    if min_ncav_margin_of_safety is not None:
        filters.append(f"{mos_expr} >= {min_ncav_margin_of_safety}")

    if require_positive_cfo:
        filters.append("r.operating_cash_flow IS NOT NULL AND r.operating_cash_flow > 0")

    if require_positive_eps:
        filters.append("r.eps IS NOT NULL AND r.eps > 0")

    if max_debt_equity is not None and max_debt_equity > 0:
        filters.append(f"(r.debt_to_equity IS NULL OR r.debt_to_equity <= {max_debt_equity})")

    if min_current_ratio > 0:
        filters.append(
            f"(s.current_liabilities IS NULL OR (s.current_assets / nullif(s.current_liabilities, 0)) >= {min_current_ratio})"
        )

    filter_sql = (chr(10) + "      AND ").join(filters)

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
            MAX(high) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) as high52,
            FIRST_VALUE(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) as first_train_close,
            ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY bar_date DESC) as rn,
            COUNT(*) OVER (PARTITION BY symbol ORDER BY bar_date) as bar_count
        FROM bars
    ),
    ratios AS (
        SELECT symbol, roce_pct, debt_to_equity, operating_margin_pct,
               eps, free_cash_flow, stockholders_equity, total_debt, operating_cash_flow
        FROM fund.financial_ratios
        WHERE fiscal_date <= '{cutoff_str}'
          AND (period_type = 'FY' OR period_type IS NULL)
        QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY fiscal_date DESC) = 1
    ),
    stmt AS (
        SELECT symbol, fiscal_date,
            MAX(CASE WHEN line_item = 'Current Assets' THEN value END) AS current_assets,
            MAX(CASE WHEN line_item = 'Total Liabilities Net Minority Interest' THEN value END) AS total_liabilities,
            MAX(CASE WHEN line_item = 'Current Liabilities' THEN value END) AS current_liabilities,
            MAX(CASE WHEN line_item = 'Total Non Current Liabilities Net Minority Interest' THEN value END) AS non_current_liabilities,
            MAX(CASE WHEN line_item = 'Diluted Average Shares' THEN value END) AS shares,
            MAX(CASE WHEN line_item = 'Cash And Cash Equivalents' THEN value END) AS cash
        FROM fund.financial_statements
        WHERE fiscal_date <= '{cutoff_str}'
          AND (period_type = 'FY' OR period_type IS NULL)
        GROUP BY symbol, fiscal_date
    ),
    stmt_latest AS (
        SELECT symbol, current_assets, total_liabilities, current_liabilities, 
               non_current_liabilities, shares, cash
        FROM stmt
        QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY fiscal_date DESC) = 1
    )
    SELECT 
        p.symbol,
        coalesce(c.company_name, p.symbol) as company_name,
        round(p.close, 2) as asof_close,
        round(p.low52, 2) as low52,
        round(p.high52, 2) as high52,
        round(((p.close / p.low52) - 1.0) * 100, 1) as pct_from_low,
        round(((p.close / p.high52) - 1.0) * 100, 1) as pct_from_high,
        round(((p.close / p.first_train_close) - 1.0) * 100, 1) as train_ret_pct,
        round(s.current_assets / 1e7, 1) as cur_assets_cr,
        round(coalesce(s.total_liabilities, s.current_liabilities + coalesce(s.non_current_liabilities, 0)) / 1e7, 1) as tot_liab_cr,
        round({ncav_expr} / 1e7, 1) as ncav_cr,
        round({ncav_ps_expr}, 2) as ncav_per_share,
        round({p_to_ncav_expr}, 2) as price_to_ncav,
        round({mos_expr}, 1) as ncav_margin_of_safety_pct,
        round(s.current_assets / nullif(s.current_liabilities, 0), 2) as current_ratio,
        round(coalesce(s.cash, 0) / nullif(s.shares, 0), 2) as cash_per_share,
        round(r.debt_to_equity, 2) as debt_to_equity,
        round(r.eps, 2) as eps,
        round(p.close / nullif(r.eps, 0), 1) as pe_ratio,
        round(p.close / nullif(r.stockholders_equity / nullif(s.shares, 0), 0), 2) as pb_ratio,
        round(coalesce(r.operating_cash_flow, 0) / 1e7, 1) as cfo_cr
    FROM prices p
    LEFT JOIN fund.companies c ON p.symbol = c.symbol
    JOIN stmt_latest s ON p.symbol = s.symbol
    LEFT JOIN ratios r ON p.symbol = r.symbol
    WHERE p.rn = 1 AND p.bar_count >= 100
      AND {filter_sql}
    ORDER BY price_to_ncav ASC, ncav_margin_of_safety_pct DESC
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
                "high52": float(r[4]),
                "pct_from_low": float(r[5]),
                "pct_from_high": float(r[6]),
                "train_ret_pct": float(r[7]),
                "cur_assets_cr": float(r[8]),
                "tot_liab_cr": float(r[9]),
                "ncav_cr": float(r[10]),
                "ncav_per_share": float(r[11]),
                "price_to_ncav": float(r[12]),
                "ncav_margin_of_safety_pct": float(r[13]),
                "current_ratio": float(r[14]) if r[14] is not None else None,
                "cash_per_share": float(r[15]),
                "debt_to_equity": float(r[16]) if r[16] is not None else None,
                "eps": float(r[17]) if r[17] is not None else None,
                "pe_ratio": float(r[18]) if r[18] is not None else None,
                "pb_ratio": float(r[19]) if r[19] is not None else None,
                "cfo_cr": float(r[20]) if r[20] is not None else 0.0,
            }
        )
    return candidates
