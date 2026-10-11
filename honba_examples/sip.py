"""honba_examples.sip: Generic Systematic Investment Plan (SIP) Backtesting Engine.

Simulates recurring monthly systematic investment and initial capital on forward price bars:
- Extracts monthly first trading session executions using DuckDB SQL.
- Supports initial capital / lump-sum corpus allocation on day 1.
- Computes volume-weighted average purchase price and share accumulation.
- Calculates terminal valuation, net PnL, and SIP return multiple.
- Computes exact annualized XIRR via bisection search.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any


def calculate_xirr(cash_flows: list[tuple[dt.date, float]]) -> float | None:
    """Calculate exact annualized Internal Rate of Return (XIRR)."""
    if len(cash_flows) < 2:
        return None
    amounts = [a for _, a in cash_flows]
    if not (any(a < 0 for a in amounts) and any(a > 0 for a in amounts)):
        return None

    d0 = cash_flows[0][0]
    years = [(d - d0).days / 365.25 for d, _ in cash_flows]

    def npv(rate: float) -> float:
        return sum(cf / ((1.0 + rate) ** y) for cf, y in zip(amounts, years))

    low, high = -0.99, 10.0
    mid = 0.0
    for _ in range(80):
        mid = (low + high) / 2.0
        val = npv(mid)
        if abs(val) < 1e-4:
            break
        if val > 0:
            low = mid
        else:
            high = mid
    return mid * 100.0


def simulate_portfolio_sip(
    catalog_dir: Path,
    symbols: list[str],
    start_date: dt.date,
    end_date: dt.date,
    monthly_amt: float = 10000.0,
    initial_capital: float = 0.0,
) -> dict[str, Any]:
    """Simulate a multi-stock forward monthly SIP with optional initial capital via DuckDB SQL."""
    import duckdb

    if not symbols:
        return {
            "summary": {
                "candidate_count": 0,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "initial_capital": initial_capital,
                "sip_amount": monthly_amt,
                "total_invested": 0.0,
                "total_value": 0.0,
                "total_profit": 0.0,
                "portfolio_multiple": 1.0,
                "portfolio_xirr": None,
            },
            "stocks": [],
        }

    catalog_glob = str(catalog_dir / "*" / "*.parquet")
    start_str = start_date.isoformat()
    end_str = end_date.isoformat()
    sym_in = ", ".join([f"'{s}'" for s in symbols])

    con = duckdb.connect()

    # 1. Fetch monthly buys
    buys_query = f"""
    WITH forward_bars AS (
        SELECT 
            regexp_extract(filename, 'NSE/([^/]+)/', 1) as symbol,
            epoch_ms(ts // 1000000)::DATE as bar_date,
            open, close
        FROM read_parquet('{catalog_glob}', filename=true)
        WHERE bar_date >= '{start_str}' AND bar_date <= '{end_str}'
          AND regexp_extract(filename, 'NSE/([^/]+)/', 1) IN ({sym_in})
    )
    SELECT symbol, bar_date, open as sip_price
    FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY symbol, date_trunc('month', bar_date) ORDER BY bar_date ASC) as rn
        FROM forward_bars
    ) WHERE rn = 1
    ORDER BY symbol, bar_date;
    """
    buy_rows = con.execute(buys_query).fetchall()

    # 2. Fetch terminal and initial prices
    term_query = f"""
    WITH forward_bars AS (
        SELECT 
            regexp_extract(filename, 'NSE/([^/]+)/', 1) as symbol,
            epoch_ms(ts // 1000000)::DATE as bar_date,
            open, close
        FROM read_parquet('{catalog_glob}', filename=true)
        WHERE bar_date >= '{start_str}' AND bar_date <= '{end_str}'
          AND regexp_extract(filename, 'NSE/([^/]+)/', 1) IN ({sym_in})
    ),
    terms AS (
        SELECT symbol, close as terminal_price, bar_date as end_date
        FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY bar_date DESC) as rn
            FROM forward_bars
        ) WHERE rn = 1
    ),
    inits AS (
        SELECT symbol, open as initial_price, bar_date as first_date
        FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY bar_date ASC) as rn
            FROM forward_bars
        ) WHERE rn = 1
    )
    SELECT t.symbol, t.terminal_price, t.end_date, i.initial_price, i.first_date
    FROM terms t
    JOIN inits i ON t.symbol = i.symbol;
    """
    term_map = {
        r[0]: (float(r[1]), r[2], float(r[3]), r[4]) for r in con.execute(term_query).fetchall()
    }

    # Group buys by symbol
    buys_by_sym: dict[str, list[tuple[dt.date, float]]] = {}
    for sym, b_date, price in buy_rows:
        buys_by_sym.setdefault(sym, []).append((b_date, float(price)))

    results: list[dict[str, Any]] = []
    portfolio_cfs: dict[dt.date, float] = {}
    stock_initial = (initial_capital / len(symbols)) if (initial_capital > 0 and symbols) else 0.0

    for sym in symbols:
        if sym not in term_map:
            continue
        term_price, last_date, init_price, first_date = term_map[sym]
        buys = buys_by_sym.get(sym, [])

        init_shares = (
            (stock_initial / init_price) if (stock_initial > 0 and init_price > 0) else 0.0
        )
        sip_shares = sum(monthly_amt / p for _, p in buys if p > 0) if monthly_amt > 0 else 0.0
        shares = init_shares + sip_shares

        sip_invested = len(buys) * monthly_amt if monthly_amt > 0 else 0.0
        total_invested = stock_initial + sip_invested
        final_val = shares * term_price
        profit = final_val - total_invested
        sip_mult = final_val / total_invested if total_invested > 0 else 1.0
        lump_mult = term_price / init_price if init_price > 0 else 1.0

        cfs: list[tuple[dt.date, float]] = []
        if stock_initial > 0:
            cfs.append((first_date, -stock_initial))
        if monthly_amt > 0:
            for d, _ in buys:
                cfs.append((d, -monthly_amt))
        cfs.append((last_date, final_val))
        xirr_val = calculate_xirr(cfs)

        for d, amt in cfs:
            portfolio_cfs[d] = portfolio_cfs.get(d, 0.0) + amt

        results.append(
            {
                "symbol": sym,
                "months": len(buys),
                "initial_capital": stock_initial,
                "sip_invested": sip_invested,
                "total_invested": total_invested,
                "shares_held": shares,
                "avg_price": total_invested / shares if shares > 0 else 0.0,
                "final_price": term_price,
                "final_val": final_val,
                "profit": profit,
                "sip_multiple": sip_mult,
                "lump_multiple": lump_mult,
                "xirr_pct": xirr_val,
                "first_date": first_date.isoformat(),
                "last_date": last_date.isoformat(),
            }
        )

    results.sort(key=lambda x: x["final_val"], reverse=True)

    tot_invested = sum(r["total_invested"] for r in results)
    tot_val = sum(r["final_val"] for r in results)
    tot_profit = tot_val - tot_invested
    port_multiple = tot_val / tot_invested if tot_invested > 0 else 1.0

    sorted_dates = sorted(portfolio_cfs.keys())
    port_cfs_list = [(d, portfolio_cfs[d]) for d in sorted_dates]
    port_xirr = calculate_xirr(port_cfs_list)

    summary = {
        "candidate_count": len(results),
        "start_date": start_str,
        "end_date": end_str,
        "initial_capital": initial_capital,
        "sip_amount": monthly_amt,
        "total_invested": tot_invested,
        "total_value": tot_val,
        "total_profit": tot_profit,
        "portfolio_multiple": port_multiple,
        "portfolio_xirr": port_xirr,
    }

    return {
        "summary": summary,
        "stocks": results,
    }
