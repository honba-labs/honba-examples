"""honba_examples.pipeline.reporting: append requested columns to candidates (``report=[...]``).

A step LOOKS AT some data to filter and separately REPORTS columns the user wants to see. Screener
SQL fixes its own output columns, so a column such as ``market_cap`` could not be shown unless the
screen filtered on it. :func:`add_report_columns` fills that gap for a fixed set of
:data:`REPORTABLE` columns, read point-in-time from ``fundamentals.duckdb``: the latest FY ratios
and statements for ONE fiscal date per symbol, with ``fiscal_date <= asof - fiscal_lag_days``
(default 90; the same rule and rounding the screeners use), with
prices taken from each candidate's own ``close``. Nothing is fabricated: a column that cannot be
derived raises a ``ValueError`` listing the reportable columns, and a symbol with no data for a
column gets ``None``.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from honba_examples.screening import find_duckdb_path
from honba_examples.screening.common import DEFAULT_FISCAL_LAG_DAYS, fiscal_cutoff

__all__ = ["REPORTABLE", "add_report_columns", "check_report_columns", "reportable_beyond"]

# column -> what it is. All are derived at the as-of date, never a live snapshot (the
# ``companies.market_cap`` / ``pe_trailing`` snapshot columns are deliberately not used).
REPORTABLE: dict[str, str] = {
    "market_cap": "as-of close x latest FY diluted average shares (INR)",
    "dividend_yield_pct": "trailing annual cash dividend per share / as-of close (%)",
    "pe_ratio": "as-of close / latest FY EPS, only when EPS > 0",
    "pb_ratio": "as-of close / (FY stockholders equity / diluted average shares)",
    "roce_pct": "latest FY return on capital employed (%)",
    "roe_pct": "latest FY return on equity (%)",
    "debt_to_equity": "latest FY debt to equity",
    "operating_margin_pct": "latest FY operating margin (%)",
    "net_margin_pct": "latest FY net margin (%)",
    "free_cash_flow": "latest FY free cash flow",
    "eps": "latest FY earnings per share",
}

# SQL (over the ``ratios``/``stmt_latest``/``px`` CTEs below) per column; rounding follows value.py.
_EXPR: dict[str, str] = {
    "market_cap": "CASE WHEN s.shares > 0 THEN round(p.close * s.shares, 0) END",
    "dividend_yield_pct": (
        "CASE WHEN s.shares > 0 THEN "
        "round(abs(coalesce(s.div_cfo, s.div_common, 0)) / s.shares / p.close * 100, 2) END"
    ),
    "pe_ratio": "CASE WHEN r.eps > 0 THEN round(p.close / r.eps, 1) END",
    "pb_ratio": (
        "CASE WHEN s.shares > 0 AND r.stockholders_equity IS NOT NULL "
        "THEN round(p.close / (r.stockholders_equity / s.shares), 2) END"
    ),
    "roce_pct": "round(r.roce_pct, 1)",
    "roe_pct": "round(r.roe_pct, 1)",
    "debt_to_equity": "round(r.debt_to_equity, 2)",
    "operating_margin_pct": "round(r.operating_margin_pct, 1)",
    "net_margin_pct": "round(r.net_margin_pct, 1)",
    "free_cash_flow": "round(r.free_cash_flow, 0)",
    "eps": "r.eps",
}


def reportable_beyond(native: Iterable[str]) -> list[str]:
    """Reportable columns a step can add, i.e. those it does not already emit itself."""
    have = set(native)
    return [c for c in REPORTABLE if c not in have]


def check_report_columns(columns: Iterable[str]) -> list[str]:
    """Return ``columns`` as a list, or raise ``ValueError`` listing the reportable ones."""
    cols = list(columns)
    unknown = [c for c in cols if c not in REPORTABLE]
    if unknown:
        raise ValueError(
            f"cannot report {unknown}: not derivable from the available data; "
            f"reportable columns are {sorted(REPORTABLE)}"
        )
    return cols


def _asof_of(candidates: Sequence[dict[str, Any]], asof: dt.date | None) -> dt.date:
    if asof is not None:
        return asof
    for c in candidates:
        if c.get("asof_date"):
            return dt.date.fromisoformat(str(c["asof_date"]))
    raise ValueError("cannot report columns without an as-of date (ctx['asof'] or asof_date)")


def add_report_columns(
    candidates: list[dict[str, Any]],
    columns: Iterable[str],
    asof: dt.date | None = None,
    *,
    db_path: Path | None = None,
    fiscal_lag_days: int = DEFAULT_FISCAL_LAG_DAYS,
) -> list[dict[str, Any]]:
    """Return ``candidates`` with ``columns`` added (copies; columns already present are kept)."""
    cols = check_report_columns(columns)
    todo = [c for c in cols if any(c not in cand for cand in candidates)]
    if not todo or not candidates:
        return candidates
    asof_date = _asof_of(candidates, asof)
    db = db_path or find_duckdb_path()
    if db is None:
        raise ValueError(
            f"cannot report {todo}: fundamentals.duckdb not found "
            f"(reportable columns are {sorted(REPORTABLE)})"
        )
    missing_close = [c["symbol"] for c in candidates if c.get("close") is None]
    if missing_close:
        raise ValueError(f"cannot report {todo}: candidates {missing_close} carry no 'close'")

    import duckdb

    cutoff = fiscal_cutoff(asof_date, fiscal_lag_days)
    select = ",\n        ".join(f"{_EXPR[c]} AS {c}" for c in todo)
    values = ", ".join("(?, ?)" for _ in candidates)
    params: list[Any] = []
    for cand in candidates:
        params += [cand["symbol"], float(cand["close"])]
    query = f"""
    WITH px(symbol, close) AS (VALUES {values}),
    ratios AS (
        SELECT symbol, fiscal_date, roce_pct, roe_pct, debt_to_equity, operating_margin_pct, net_margin_pct,
               eps, free_cash_flow, stockholders_equity
        FROM fund.financial_ratios
        WHERE fiscal_date <= '{cutoff}' AND (period_type = 'FY' OR period_type IS NULL)
        QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY fiscal_date DESC) = 1
    ),
    stmt AS (
        SELECT symbol, fiscal_date,
            MAX(CASE WHEN line_item = 'Cash Dividends Paid' THEN value END) AS div_cfo,
            MAX(CASE WHEN line_item = 'Common Stock Dividend Paid' THEN value END) AS div_common,
            MAX(CASE WHEN line_item = 'Diluted Average Shares' THEN value END) AS shares
        FROM fund.financial_statements
        WHERE fiscal_date <= '{cutoff}' AND (period_type = 'FY' OR period_type IS NULL)
        GROUP BY symbol, fiscal_date
    ),
    s AS (
        SELECT st.symbol, st.div_cfo, st.div_common, st.shares
        FROM stmt st
        JOIN ratios r2 ON st.symbol = r2.symbol AND st.fiscal_date = r2.fiscal_date
    )
    SELECT p.symbol,
        {select}
    FROM px p
    LEFT JOIN ratios r ON p.symbol = r.symbol
    LEFT JOIN s ON p.symbol = s.symbol
    """
    con = duckdb.connect()
    con.execute(f"ATTACH '{db}' AS fund (READ_ONLY)")
    found = {row[0]: row[1:] for row in con.execute(query, params).fetchall()}
    out: list[dict[str, Any]] = []
    for cand in candidates:
        row = dict(cand)
        values_for = found.get(cand["symbol"], (None,) * len(todo))
        for name, value in zip(todo, values_for, strict=True):
            row.setdefault(name, float(value) if value is not None else None)
        out.append(row)
    return out
