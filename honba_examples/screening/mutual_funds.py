"""Point-in-time Mutual Fund screener with Equity DB holdings lookthrough."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from honba_data.store.mutual_funds import DuckDbMutualFundsStore

from honba_examples.screening.paths import find_duckdb_path, find_mutual_funds_db_path

__all__ = ["screen_mutual_funds"]


def screen_mutual_funds(
    asof_date: dt.date | None = None,
    category: str = "all",
    plan: str = "Direct",
    option: str = "Growth",
    min_aum_cr: float | None = 500.0,
    max_aum_cr: float | None = None,
    max_ter: float | None = 1.0,
    min_rolling_sharpe: float | None = None,
    min_alpha: float | None = None,
    max_downside_capture: float | None = None,
    min_portfolio_roce: float | None = None,
    max_portfolio_pe: float | None = None,
    holding_symbol: str | None = None,
    max_overlap_with: tuple[str, float] | None = None,
    disclosure_lag_days: int = 10,
    top: int | None = 10,
    mf_db_path: Path | None = None,
    equity_db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Screen mutual funds point-in-time with underlying equity portfolio lookthrough.

    Features:
    1. Category & Plan filtering (SEBI 2017 canonical categories, Direct vs Regular).
    2. AUM and TER cost filters.
    3. Equity Lookthrough Fundamentals:
       - Cross-references ``mf_holdings.isin`` with ``companies.isin_code`` in ``fundamentals.duckdb``.
       - Evaluates weighted portfolio ROCE, weighted portfolio P/E, and market cap breakdown.
    4. Reverse Equity Lookup:
       - Filter funds that hold a specified equity symbol (e.g., RELIANCE, DIXON, INFY).
    5. Portfolio Overlap Exclusion:
       - Ensure selected funds have low correlation/overlap with an existing holding (e.g. < 30% overlap).
    """
    db_file = mf_db_path or find_mutual_funds_db_path()
    if db_file is None or not db_file.is_file():
        # Auto-initialize store and seed reference data if not found
        store = DuckDbMutualFundsStore()
        store.seed_reference_data()
    else:
        store = DuckDbMutualFundsStore(db_file)

    eq_db = equity_db_path or find_duckdb_path()
    asof = asof_date or dt.datetime.now(dt.timezone.utc).date()

    schemes = store.get_schemes(category=category, plan=plan, option=option)

    candidates: list[dict[str, Any]] = []

    for s in schemes:
        code = s["scheme_code"]
        name = s["scheme_name"]
        cat = s["category"]

        # 1. Fetch latest stats on or before asof
        import duckdb

        con = duckdb.connect(str(store.db_path), read_only=True)
        stat_row = con.execute(
            """
            SELECT nav, ter_pct, aum_cr, portfolio_turnover_pct, asof_date
            FROM mf_fund_stats
            WHERE scheme_code = ? AND asof_date <= ?
            ORDER BY asof_date DESC LIMIT 1
            """,
            [code, asof.isoformat()],
        ).fetchone()
        con.close()

        if not stat_row:
            continue

        nav, ter, aum, turnover, _ = stat_row
        nav = float(nav)
        ter = float(ter)
        aum = float(aum)
        turnover = float(turnover)

        # Apply basic fund-level gates
        if min_aum_cr is not None and aum < min_aum_cr:
            continue
        if max_aum_cr is not None and aum > max_aum_cr:
            continue
        if max_ter is not None and ter > max_ter:
            continue

        # 2. Portfolio Lookthrough Cross-Referencing
        lookthrough = store.get_portfolio_fundamentals(
            scheme_code=code,
            asof_date=asof,
            equity_db_path=eq_db,
            disclosure_lag_days=disclosure_lag_days,
        )

        port_roce = lookthrough.get("portfolio_roce")
        port_pe = lookthrough.get("portfolio_pe")
        holdings = lookthrough.get("holdings", [])
        holding_symbols = {h["symbol"].upper() for h in holdings}

        # Apply Lookthrough Equity Filters
        if min_portfolio_roce is not None and (port_roce is None or port_roce < min_portfolio_roce):
            continue

        if max_portfolio_pe is not None and (port_pe is None or port_pe > max_portfolio_pe):
            continue

        if holding_symbol:
            target_sym = holding_symbol.strip().upper()
            if target_sym not in holding_symbols:
                continue

        # Apply Overlap Filter
        overlap_pct = 0.0
        if max_overlap_with:
            ref_scheme, max_allowed = max_overlap_with
            overlap_res = store.get_portfolio_overlap(code, ref_scheme, asof_date=asof)
            overlap_pct = overlap_res.get("overlap_pct", 0.0)
            if overlap_pct > max_allowed:
                continue

        # Derive scoring & rank
        # Higher portfolio ROCE, lower TER, solid AUM size
        score = (port_roce or 15.0) * 1.5 - (ter * 10.0)

        candidates.append(
            {
                "scheme_code": code,
                "scheme_name": name,
                "category": cat,
                "plan": s["plan"],
                "option_type": s["option_type"],
                "amc_name": s["amc_name"],
                "nav": round(nav, 2),
                "ter_pct": round(ter, 2),
                "aum_cr": round(aum, 1),
                "portfolio_turnover_pct": round(turnover, 1),
                "portfolio_roce": port_roce,
                "portfolio_pe": port_pe,
                "portfolio_debt_to_equity": lookthrough.get("portfolio_debt_to_equity"),
                "large_cap_pct": lookthrough.get("large_cap_pct", 0.0),
                "mid_cap_pct": lookthrough.get("mid_cap_pct", 0.0),
                "small_cap_pct": lookthrough.get("small_cap_pct", 0.0),
                "coverage_pct": lookthrough.get("coverage_pct", 0.0),
                "overlap_pct": round(overlap_pct, 1),
                "top_holdings": [h["symbol"] for h in holdings[:5]],
                "score": round(score, 1),
                "asof_date": asof.isoformat(),
            }
        )

    # Rank candidates by multi-factor score descending
    candidates.sort(key=lambda c: c["score"], reverse=True)

    if top and top > 0:
        return candidates[:top]
    return candidates
