"""honba_examples.sip: Generic Systematic Investment Plan (SIP) Backtesting Engine.

Simulates recurring monthly systematic investment and initial capital deployment on forward price bars:
- Deploys initial capital lump sum on `--asof` date across screened candidates.
- Executes monthly systematic investment plan (SIP) installments for every subsequent month until till date.
- Prunes DuckDB queries strictly to target symbol Parquet files for sub-50ms execution.
- Computes volume-weighted average purchase price and share accumulation.
- Calculates terminal valuation, net PnL, peak multiple, and SIP return multiple.
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
    monthly_amt: float | None = None,
    capital_budget: float | None = None,
    *,
    initial_capital: float | None = None,
    monthly_sip: float | None = None,
    sip_mode: str = "portfolio",
) -> dict[str, Any]:
    """Simulate initial capital deployment on start_date and monthly SIP until end_date via DuckDB SQL."""
    import duckdb

    # Backward compatibility mappings
    if initial_capital is None and capital_budget is not None:
        initial_capital = capital_budget
    if monthly_sip is None and monthly_amt is not None:
        monthly_sip = monthly_amt

    # Calculate calendar month span of the out-of-sample window
    cal_months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
    window_months = max(cal_months, 1)

    if not symbols:
        return {
            "summary": {
                "candidate_count": 0,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "window_months": window_months,
                "initial_capital": 0.0,
                "monthly_sip": monthly_sip or 0.0,
                "portfolio_monthly_sip": monthly_sip or 0.0,
                "monthly_sip_per_stock": 0.0,
                "sip_amount": monthly_sip or 0.0,
                "sip_mode": sip_mode,
                "total_sip_invested": 0.0,
                "total_invested": 0.0,
                "total_value": 0.0,
                "total_profit": 0.0,
                "portfolio_multiple": 1.0,
                "portfolio_xirr": None,
            },
            "stocks": [],
        }

    # Prune Parquet files strictly to target symbols for instant query speed
    target_files: list[str] = []
    for s in symbols:
        sym_dir = catalog_dir / s
        if sym_dir.is_dir():
            target_files.extend([str(f) for f in sym_dir.glob("*.parquet")])
    if not target_files:
        target_files = [str(catalog_dir / "*" / "*.parquet")]

    start_str = start_date.isoformat()
    end_str = end_date.isoformat()
    con = duckdb.connect()

    # 1. Fetch monthly first trading session executions
    buys_query = f"""
    WITH forward_bars AS (
        SELECT 
            regexp_extract(filename, 'NSE/([^/]+)/', 1) as symbol,
            epoch_ms(ts // 1000000)::DATE as bar_date,
            open, close
        FROM read_parquet({target_files}, filename=true)
        WHERE bar_date >= '{start_str}' AND bar_date <= '{end_str}'
    )
    SELECT symbol, bar_date, open as sip_price
    FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY symbol, date_trunc('month', bar_date) ORDER BY bar_date ASC) as rn
        FROM forward_bars
    ) WHERE rn = 1
    ORDER BY symbol, bar_date;
    """
    buy_rows = con.execute(buys_query).fetchall()

    # Group buys by symbol
    buys_by_sym: dict[str, list[tuple[dt.date, float]]] = {}
    for sym, b_date, price in buy_rows:
        buys_by_sym.setdefault(sym, []).append((b_date, float(price)))

    n_syms = len(symbols)

    # Initial lump-sum capital per stock deployed on start_date
    if initial_capital is not None and initial_capital > 0:
        init_per_stock = float(initial_capital) / n_syms
    else:
        init_per_stock = 0.0

    # Monthly SIP installment: either portfolio-level (divided equally) or per-stock
    if monthly_sip is not None and monthly_sip > 0:
        if sip_mode == "portfolio":
            sip_per_stock = float(monthly_sip) / n_syms
            port_monthly_sip = float(monthly_sip)
        else:
            sip_per_stock = float(monthly_sip)
            port_monthly_sip = float(monthly_sip) * n_syms
    elif init_per_stock == 0.0:
        # Fallback default if neither initial capital nor SIP is specified
        port_monthly_sip = 10000.0
        sip_per_stock = port_monthly_sip / n_syms if sip_mode == "portfolio" else 10000.0
    else:
        sip_per_stock = 0.0
        port_monthly_sip = 0.0

    # 2. Fetch terminal, peak, and initial prices for forward confirmation metrics
    term_query = f"""
    WITH forward_bars AS (
        SELECT 
            regexp_extract(filename, 'NSE/([^/]+)/', 1) as symbol,
            epoch_ms(ts // 1000000)::DATE as bar_date,
            open, close, high
        FROM read_parquet({target_files}, filename=true)
        WHERE bar_date >= '{start_str}' AND bar_date <= '{end_str}'
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
    ),
    peaks AS (
        SELECT symbol, MAX(high) as peak_price
        FROM forward_bars
        GROUP BY symbol
    )
    SELECT t.symbol, t.terminal_price, t.end_date, i.initial_price, i.first_date, p.peak_price
    FROM terms t
    JOIN inits i ON t.symbol = i.symbol
    JOIN peaks p ON t.symbol = p.symbol;
    """
    term_map = {
        r[0]: (float(r[1]), r[2], float(r[3]), r[4], float(r[5]))
        for r in con.execute(term_query).fetchall()
    }

    results: list[dict[str, Any]] = []
    portfolio_cfs: dict[dt.date, float] = {}

    for sym in symbols:
        if sym not in term_map:
            continue
        term_price, last_date, init_price, first_date, peak_price = term_map[sym]
        buys = buys_by_sym.get(sym, [])

        shares = 0.0
        total_invested = 0.0
        init_invested = 0.0
        sip_invested = 0.0
        cfs: list[tuple[dt.date, float]] = []

        # Step 1: Deploy initial capital lump-sum on first trading date (on/after start_date)
        if init_per_stock > 0 and init_price > 0:
            shares += init_per_stock / init_price
            total_invested += init_per_stock
            init_invested = init_per_stock
            cfs.append((first_date, -init_per_stock))
            portfolio_cfs[first_date] = portfolio_cfs.get(first_date, 0.0) - init_per_stock

        # Step 2: Execute monthly SIP installments for each period
        # If initial capital was deployed in the starting month, SIP starts in subsequent months
        if init_invested > 0:
            sip_buys = [
                b for b in buys if (b[0].year, b[0].month) > (first_date.year, first_date.month)
            ]
        else:
            sip_buys = buys

        executed_sip_count = 0
        if sip_per_stock > 0:
            for b_date, p in sip_buys:
                if p <= 0:
                    continue
                shares += sip_per_stock / p
                total_invested += sip_per_stock
                sip_invested += sip_per_stock
                executed_sip_count += 1
                cfs.append((b_date, -sip_per_stock))
                portfolio_cfs[b_date] = portfolio_cfs.get(b_date, 0.0) - sip_per_stock

        final_val = shares * term_price
        profit = final_val - total_invested
        sip_mult = final_val / total_invested if total_invested > 0 else 1.0
        lump_mult = term_price / init_price if init_price > 0 else 1.0
        peak_mult = peak_price / init_price if init_price > 0 else 1.0

        # Confirmation badge based on forward peak or terminal multiple
        if peak_mult >= 3.0 or lump_mult >= 3.0:
            confirmation = "★ 3x+ Multibagger"
        elif peak_mult >= 2.0 or lump_mult >= 2.0:
            confirmation = "★ 2x Multibagger"
        elif peak_mult >= 1.5 or lump_mult >= 1.5:
            confirmation = "Outperformer (1.5x+)"
        elif lump_mult >= 1.0:
            confirmation = "Modest Gain"
        else:
            confirmation = "Declined"

        if total_invested > 0:
            cfs.append((last_date, final_val))
            xirr_val = calculate_xirr(cfs)
        else:
            xirr_val = None

        # Displayed months reflects executed SIP cycles, or window span
        display_months = executed_sip_count if executed_sip_count > 0 else window_months

        results.append(
            {
                "symbol": sym,
                "months": display_months,
                "init_capital": init_invested,
                "sip_amount": sip_per_stock,
                "sip_count": executed_sip_count,
                "total_invested": total_invested,
                "shares_held": shares,
                "avg_price": total_invested / shares if shares > 0 else 0.0,
                "initial_price": init_price,
                "final_price": term_price,
                "peak_price": peak_price,
                "final_val": final_val,
                "profit": profit,
                "sip_multiple": sip_mult,
                "lump_multiple": lump_mult,
                "peak_multiple": peak_mult,
                "xirr_pct": xirr_val,
                "confirmation": confirmation,
                "first_date": first_date.isoformat(),
                "last_date": last_date.isoformat(),
            }
        )

    results.sort(key=lambda x: x["final_val"], reverse=True)

    tot_init_capital = sum(r["init_capital"] for r in results)
    tot_sip_invested = sum(r["total_invested"] - r["init_capital"] for r in results)
    tot_invested = tot_init_capital + tot_sip_invested
    tot_val = sum(r["final_val"] for r in results)
    tot_profit = tot_val - tot_invested
    port_multiple = tot_val / tot_invested if tot_invested > 0 else 1.0

    sorted_dates = sorted(portfolio_cfs.keys())
    port_cfs_list = [(d, portfolio_cfs[d]) for d in sorted_dates]
    if tot_val > 0 and len(results) > 0:
        last_session = max(r["last_date"] for r in results)
        port_cfs_list.append((dt.date.fromisoformat(last_session), tot_val))
    port_xirr = calculate_xirr(port_cfs_list)

    summary = {
        "candidate_count": len(results),
        "start_date": start_str,
        "end_date": end_str,
        "window_months": window_months,
        "initial_capital": tot_init_capital,
        "monthly_sip": port_monthly_sip,
        "portfolio_monthly_sip": port_monthly_sip,
        "monthly_sip_per_stock": sip_per_stock,
        "sip_amount": port_monthly_sip,
        "sip_mode": sip_mode,
        "total_sip_invested": tot_sip_invested,
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
