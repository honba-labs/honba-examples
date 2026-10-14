"""honba_examples.sip: Generic Systematic Investment Plan (SIP) Backtesting Engine.

Simulates recurring monthly systematic investment and initial capital deployment on forward price bars:
- Deploys the initial capital lump sum at the next session's open after the `--asof` date (the
  screen saw that day's close) across screened candidates.
- Reports symbols it could not price: `unpriced_symbols` (no bar after asof), `skipped_symbols`
  (no data) and `delisted_symbols` (bars stop early); see `sip_data_notes`.
- Supports customizable portfolio weighting (equal weighting or ratio to 52-week high).
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

from honba.strategies.base import Parameter, Strategy

__all__ = [
    "SipPortfolioStrategy",
    "allocation_weights",
    "calculate_xirr",
    "simulate_portfolio_sip",
    "sip_data_notes",
]


class SipPortfolioStrategy(Strategy):
    """Systematic Investment Plan (SIP) strategy implementing Honba's Strategy ABC.

    Models monthly capital allocation and share accumulation on bar events.
    """

    name = "sip_portfolio_strategy"
    warmup_bars = 0

    monthly_amount = Parameter(
        default=10000.0,
        min=0.0,
        max=1000000.0,
        step=1000.0,
        description="Monthly SIP allocation amount in INR per stock",
    )
    initial_capital = Parameter(
        default=0.0,
        min=0.0,
        max=100000000.0,
        step=10000.0,
        description="Total initial capital budget in INR",
    )

    def __init__(self, monthly_amount: float = 10000.0, initial_capital: float = 0.0) -> None:
        super().__init__()
        self._monthly_amount = monthly_amount
        self._initial_capital = initial_capital
        self._last_month: int | None = None

    def on_bar(self, bar: Any) -> None:
        bar_date = getattr(bar, "date", None)
        if bar_date is None:
            return
        if self._last_month != bar_date.month and self._monthly_amount > 0:
            self._last_month = bar_date.month
            self.buy(bar.instrument_id, 1)


STALE_DAYS = 10  # a last bar this many calendar days before the other names' last session = stale


def sip_data_notes(summary: dict[str, Any]) -> list[str]:
    """Human-readable warnings for the symbols the simulation could not price (empty if none)."""
    notes: list[str] = []
    unpriced = summary.get("unpriced_symbols") or []
    if unpriced:
        notes.append(
            f"No bars after the start date for {', '.join(unpriced)}: they could not be bought "
            "(delisted or halted?) and are held flat as idle cash at their last close."
        )
    stale = [s for s in (summary.get("delisted_symbols") or []) if s not in unpriced]
    if stale:
        notes.append(
            f"Bars stop well before the window ends for {', '.join(stale)}: valued at their last "
            "close (delisted or halted?), which may overstate the recovery value."
        )
    skipped = summary.get("skipped_symbols") or []
    if skipped:
        notes.append(f"No data at all for {', '.join(skipped)}: skipped.")
    return notes


def allocation_weights(allocation: str, candidates: list[dict[str, Any]]) -> dict[str, float]:
    """Normalised portfolio weights per symbol for an allocation model.

    - ``52w-ratio``: inverse distance from the 52-week high (tighter base, bigger weight).
    - ``score``: margin of safety (value screens) or multibagger score, floored at 1.
    - ``equal``: 1/N.

    Returns ``{}`` for no candidates or an unknown model (callers then fall back to equal).
    """
    if not candidates:
        return {}
    if allocation == "52w-ratio":
        raw_w = {
            c["symbol"]: 1.0 / max(abs(float(c.get("pct_from_high", -5.0))), 0.1)
            for c in candidates
        }
    elif allocation == "score":
        raw_w = {
            c["symbol"]: max(float(c.get("margin_of_safety_pct", c.get("mb_score", 10.0))), 1.0)
            for c in candidates
        }
    elif allocation == "equal":
        return {c["symbol"]: 1.0 / len(candidates) for c in candidates}
    else:
        return {}
    tot_w = sum(raw_w.values())
    return {s: v / tot_w for s, v in raw_w.items()}


def calculate_xirr(cash_flows: list[tuple[dt.date, float]]) -> float | None:
    """Calculate exact annualized Internal Rate of Return (XIRR)."""
    if len(cash_flows) < 2:
        return None
    amounts = [a for _, a in cash_flows]
    if not (any(a < 0 for a in amounts) and any(a > 0 for a in amounts)):
        return None

    d0 = cash_flows[0][0]
    if (cash_flows[-1][0] - d0).days < 1:
        return 0.0 if abs(sum(amounts)) < 1e-4 else None

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
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Simulate initial capital deployment and monthly SIP until ``end_date`` via DuckDB SQL.

    ``start_date`` is the screening date (``--asof``): the screen read that day's close, so the
    lump sum buys at the **next session's open**, and no bar on or before ``start_date`` is traded.
    Symbols with no bar after ``start_date`` cannot be bought; they are listed in
    ``summary["unpriced_symbols"]`` (rows ``status="unpriced"``) and held flat as idle cash at their
    last close. Symbols with no data at all are in ``summary["skipped_symbols"]``. Symbols whose
    bars stop well before the last session of the others are flagged ``delisted`` (``status="stale"``)
    and valued at their last close; all flagged names are in ``summary["delisted_symbols"]``.
    """
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
                "unpriced_symbols": [],
                "skipped_symbols": [],
                "delisted_symbols": [],
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
    WITH forward_bars AS (\n        SELECT \n            regexp_extract(filename, 'NSE/([^/]+)/', 1) as symbol,\n            epoch_ms(ts // 1000000)::DATE as bar_date,\n            open, close\n        FROM read_parquet({target_files}, filename=true)\n        WHERE bar_date > '{start_str}' AND bar_date <= '{end_str}'\n    )\n    SELECT symbol, bar_date, open as sip_price\n    FROM (\n        SELECT *, ROW_NUMBER() OVER (PARTITION BY symbol, date_trunc('month', bar_date) ORDER BY bar_date ASC) as rn\n        FROM forward_bars\n    ) WHERE rn = 1\n    ORDER BY symbol, bar_date;\n    """
    buy_rows = con.execute(buys_query).fetchall()

    # Group buys by symbol
    buys_by_sym: dict[str, list[tuple[dt.date, float]]] = {}
    for sym, b_date, price in buy_rows:
        buys_by_sym.setdefault(sym, []).append((b_date, float(price)))

    n_syms = len(symbols)

    # Normalize weights across available symbols
    effective_weights: dict[str, float] = {}
    if weights:
        sum_w = sum(weights.get(s, 0.0) for s in symbols)
        if sum_w > 0:
            effective_weights = {s: weights.get(s, 0.0) / sum_w for s in symbols}
    if not effective_weights:
        effective_weights = {s: 1.0 / n_syms for s in symbols}

    # Monthly portfolio SIP base amount
    if monthly_sip is not None and monthly_sip > 0:
        if sip_mode == "portfolio":
            port_monthly_sip = float(monthly_sip)
        else:
            port_monthly_sip = float(monthly_sip) * n_syms
    elif initial_capital is None or initial_capital <= 0:
        port_monthly_sip = 10000.0
    else:
        port_monthly_sip = 0.0

    # 2. Fetch terminal, peak, and initial prices for forward confirmation metrics
    term_query = f"""
    WITH forward_bars AS (\n        SELECT \n            regexp_extract(filename, 'NSE/([^/]+)/', 1) as symbol,\n            epoch_ms(ts // 1000000)::DATE as bar_date,\n            open, close, high\n        FROM read_parquet({target_files}, filename=true)\n        WHERE bar_date > '{start_str}' AND bar_date <= '{end_str}'\n    ),\n    terms AS (\n        SELECT symbol, close as terminal_price, bar_date as end_date\n        FROM (\n            SELECT *, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY bar_date DESC) as rn\n            FROM forward_bars\n        ) WHERE rn = 1\n    ),\n    inits AS (\n        SELECT symbol, open as initial_price, bar_date as first_date\n        FROM (\n            SELECT *, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY bar_date ASC) as rn\n            FROM forward_bars\n        ) WHERE rn = 1\n    ),\n    peaks AS (\n        SELECT symbol, MAX(high) as peak_price\n        FROM forward_bars\n        GROUP BY symbol\n    )\n    SELECT t.symbol, t.terminal_price, t.end_date, i.initial_price, i.first_date, p.peak_price\n    FROM terms t\n    JOIN inits i ON t.symbol = i.symbol\n    JOIN peaks p ON t.symbol = p.symbol;\n    """
    term_map = {
        r[0]: (float(r[1]), r[2], float(r[3]), r[4], float(r[5]))
        for r in con.execute(term_query).fetchall()
    }

    # For symbols missing from forward_bars (e.g. asof on a weekend/latest data date)
    missing_syms = [s for s in symbols if s not in term_map]
    unpriced: set[str] = set()
    if missing_syms:
        missing_files = []
        for s in missing_syms:
            sym_dir = catalog_dir / s
            if sym_dir.is_dir():
                missing_files.extend([str(f) for f in sym_dir.glob("*.parquet")])
        if missing_files:
            latest_query = f"""
            SELECT symbol, close, bar_date\n            FROM (\n                SELECT \n                    regexp_extract(filename, 'NSE/([^/]+)/', 1) as symbol,\n                    epoch_ms(ts // 1000000)::DATE as bar_date,\n                    close,\n                    ROW_NUMBER() OVER (PARTITION BY regexp_extract(filename, 'NSE/([^/]+)/', 1) ORDER BY ts DESC) as rn\n                FROM read_parquet({missing_files}, filename=true)\n                WHERE epoch_ms(ts // 1000000)::DATE <= '{start_str}'\n            )\n            WHERE rn = 1;\n            """
            for row in con.execute(latest_query).fetchall():
                sym = row[0]
                px = float(row[1])
                b_date = row[2]
                term_map[sym] = (px, b_date, px, b_date, px)
                unpriced.add(sym)
    skipped = [s for s in symbols if s not in term_map]
    priced_ends = [v[1] for k, v in term_map.items() if k not in unpriced]
    reference_end = max(priced_ends) if priced_ends else None

    results: list[dict[str, Any]] = []
    portfolio_cfs: dict[dt.date, float] = {}

    for sym in symbols:
        if sym not in term_map:
            continue
        term_price, last_date, init_price, first_date, peak_price = term_map[sym]
        buys = buys_by_sym.get(sym, [])

        w = effective_weights.get(sym, 1.0 / n_syms)

        # Allocate initial capital and monthly SIP based on weight
        init_stock_amt = (
            (float(initial_capital) * w)
            if (initial_capital is not None and initial_capital > 0)
            else 0.0
        )
        if port_monthly_sip > 0:
            sip_stock_amt = (
                (port_monthly_sip * w)
                if sip_mode == "portfolio"
                else (float(monthly_sip or 10000.0))
            )
        else:
            sip_stock_amt = 0.0

        shares = 0.0
        total_invested = 0.0
        init_invested = 0.0
        sip_invested = 0.0
        cfs: list[tuple[dt.date, float]] = []

        # Step 1: Deploy initial capital lump-sum on first trading date (on/after start_date)
        if init_stock_amt > 0 and init_price > 0:
            shares += init_stock_amt / init_price
            total_invested += init_stock_amt
            init_invested = init_stock_amt
            cfs.append((first_date, -init_stock_amt))
            portfolio_cfs[first_date] = portfolio_cfs.get(first_date, 0.0) - init_stock_amt

        # Step 2: Execute monthly SIP installments for each period
        if init_invested > 0:
            sip_buys = [
                b for b in buys if (b[0].year, b[0].month) > (first_date.year, first_date.month)
            ]
        else:
            sip_buys = buys

        executed_sip_count = 0
        if sip_stock_amt > 0:
            for b_date, p in sip_buys:
                if p <= 0:
                    continue
                shares += sip_stock_amt / p
                total_invested += sip_stock_amt
                sip_invested += sip_stock_amt
                executed_sip_count += 1
                cfs.append((b_date, -sip_stock_amt))
                portfolio_cfs[b_date] = portfolio_cfs.get(b_date, 0.0) - sip_stock_amt

        final_val = shares * term_price
        profit = final_val - total_invested
        sip_mult = final_val / total_invested if total_invested > 0 else 1.0
        lump_mult = term_price / init_price if init_price > 0 else 1.0
        peak_mult = peak_price / init_price if init_price > 0 else 1.0

        # Confirmation badge based on forward peak or terminal multiple
        is_unpriced = sym in unpriced
        is_stale = (
            not is_unpriced
            and reference_end is not None
            and (reference_end - last_date).days > STALE_DAYS
        )
        if is_unpriced:
            confirmation = "⚠ No bars after asof (delisted?)"
        elif is_stale:
            confirmation = f"⚠ Delisted/halted (last bar {last_date.isoformat()})"
        elif peak_mult >= 3.0 or lump_mult >= 3.0:
            confirmation = "★ 3x+ Multibagger"
        elif peak_mult >= 2.0 or lump_mult >= 2.0:
            confirmation = "★ 2x Multibagger"
        elif peak_mult >= 1.5 or lump_mult >= 1.5:
            confirmation = "Outperformer (1.5x+)"
        elif lump_mult >= 1.0:
            confirmation = "Modest Gain"
        else:
            confirmation = "Declined"

        if total_invested > 0 and final_val > 0:
            cfs.append((last_date, final_val))
            xirr_val = calculate_xirr(cfs)
        else:
            xirr_val = None

        results.append(
            {
                "symbol": sym,
                "weight_pct": round(w * 100.0, 1),
                "months": window_months,
                "init_capital": init_invested,
                "sip_amount": sip_stock_amt,
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
                "status": "unpriced" if is_unpriced else ("stale" if is_stale else "ok"),
                "delisted": is_unpriced or is_stale,
                "first_date": first_date.isoformat(),
                "last_date": last_date.isoformat(),
            }
        )

    if not results and symbols:
        for sym in symbols:
            w = effective_weights.get(sym, 1.0 / n_syms)
            init_stock_amt = (
                (float(initial_capital) * w)
                if (initial_capital is not None and initial_capital > 0)
                else 0.0
            )
            sip_stock_amt = (
                (port_monthly_sip * w)
                if sip_mode == "portfolio"
                else (float(monthly_sip or 10000.0))
            )
            results.append(
                {
                    "symbol": sym,
                    "weight_pct": round(w * 100.0, 1),
                    "months": 1,
                    "init_capital": init_stock_amt,
                    "sip_amount": sip_stock_amt,
                    "sip_count": 0,
                    "total_invested": init_stock_amt,
                    "shares_held": 0.0,
                    "avg_price": 0.0,
                    "initial_price": 0.0,
                    "final_price": 0.0,
                    "peak_price": 0.0,
                    "final_val": init_stock_amt,
                    "profit": 0.0,
                    "sip_multiple": 1.0,
                    "lump_multiple": 1.0,
                    "peak_multiple": 1.0,
                    "xirr_pct": 0.0,
                    "confirmation": "No data (nothing was bought)",
                    "status": "no_data",
                    "delisted": False,
                    "first_date": start_str,
                    "last_date": end_str,
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
        "monthly_sip_per_stock": (port_monthly_sip / n_syms) if n_syms > 0 else 0.0,
        "sip_amount": port_monthly_sip,
        "sip_mode": sip_mode,
        "total_sip_invested": tot_sip_invested,
        "total_invested": tot_invested,
        "total_value": tot_val,
        "total_profit": tot_profit,
        "portfolio_multiple": port_multiple,
        "portfolio_xirr": port_xirr,
        "unpriced_symbols": sorted(unpriced),
        "skipped_symbols": skipped,
        "delisted_symbols": sorted(r["symbol"] for r in results if r["delisted"]),
    }

    return {
        "summary": summary,
        "stocks": results,
    }
