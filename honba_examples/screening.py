"""honba_examples.screening: Generic Point-in-Time Screener Engine.

Provides zero-lookahead screening primitives powered by DuckDB SQL:
- `screen_multibaggers`: Prospective "going-to-be" multibagger screener identifying early Stage 2
  breakouts, unexhausted consolidation bases, volume surges, and capital efficiency prior to --asof.
- `screen_value_and_dividend`: Contrarian value & high dividend yield screener near 52-week low.
- `screen_intelligent_investor`: Benjamin Graham's value screener (Defensive & Enterprising)
  enforcing Graham Number, Margin of Safety, financial condition, and valuation discipline.
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
    max_return_1y: float | None = None,
    max_pct_from_high: float = 15.0,
    max_debt_to_equity: float = 1.5,
    top: int | None = None,
) -> list[dict[str, Any]]:
    """Screen prospective 'going-to-be' multibaggers strictly as of asof_date.

    Identifies Stage 2 breakout momentum leaders before late-stage exhaustion:
    1. Trend template: Price >= SMA50 >= SMA200, with rising 200 SMA.
    2. Consolidation base: Price within 15% of 52-week high.
    3. Momentum acceleration: Positive 3-month momentum (> 0%).
    4. Volume accumulation: Recent 20-day volume expansion over 200-day baseline.
    5. Fundamental discipline: ROCE capital efficiency and debt coverage.
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

    max_ret_filter = (
        f"AND ((m.close / m.close_1y) - 1.0) * 100 <= {max_return_1y}"
        if max_return_1y is not None
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
            symbol, close, sma50, sma200, sma200_prior, high52, first_train_close,
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
            coalesce(least({"r.roce_pct" if db_path else "15.0"}, 20.0) * 0.5, 5.0),
            1
        ) as mb_score
    FROM metrics m
    {fund_join}
    WHERE m.rn = 1 AND m.bar_count >= 252
      AND m.close >= m.sma50
      AND m.sma50 >= m.sma200
      AND m.sma200 >= m.sma200_prior
      AND m.close >= m.close_3m
      AND ((m.close / m.close_1y) - 1.0) * 100 >= {min_return_1y}
      AND ((m.close / m.high52) - 1.0) * 100 >= -{abs(max_pct_from_high)}
      {max_ret_filter}
      AND ({"r.debt_to_equity IS NULL OR r.debt_to_equity <= " + str(max_debt_to_equity) if db_path else "1=1"})
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
        SELECT DISTINCT symbol
        FROM (
            SELECT symbol, low,
                   MIN(low) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) as low52
            FROM bars
            WHERE bar_date >= ('{asof_str}'::DATE - INTERVAL 30 DAY)
        )
        WHERE low <= low52 * 1.02
    ){fund_ctes}
    SELECT 
        p.symbol,
        {"coalesce(c.company_name, p.symbol)" if use_fund else "p.symbol"} as company_name,
        round(p.close, 2) as asof_close,
        round(p.low52, 2) as low52,
        round(((p.close / p.low52) - 1.0) * 100, 1) as pct_from_low,
        round(((p.close / p.first_train_close) - 1.0) * 100, 1) as train_ret_pct,
        (t.symbol IS NOT NULL) as hit_52w_low_recently,
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
