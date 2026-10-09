"""Equity-curve statistics shared by the universe backtests (07 and 08).

A curve is a list of ``{"date": "YYYY-MM-DD", "value": float, "cash": float}``
points, one per session close. Values are floats: these are statistics, not
ledger entries (ADR 0011), so callers convert from paise before calling.

Returns are session over session, Sharpe is annualised over
252 sessions with a zero risk-free rate and the sample (n-1) deviation, CAGR
uses calendar days / 365.25 with a one-day floor, and drawdown is reported as a
negative percentage.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["SESSIONS_PER_YEAR", "curve_metrics", "exposure_metrics", "max_drawdown", "sharpe_ratio"]

SESSIONS_PER_YEAR = 252


def curve_metrics(
    curve: Sequence[dict[str, Any]],
    capital: float,
    *,
    cash_inflows: Mapping[dt.date | str, float] | None = None,
    total_invested: float | None = None,
) -> dict[str, float]:
    """Final value, total return %, CAGR %, max drawdown % (<= 0) and Sharpe.

    When ``cash_inflows`` (e.g. SIP deposits) is provided, session returns use Time-Weighted
    Return (TWR) formulation: ``(V_t - Inflow_t - V_{t-1}) / V_{t-1}`` to avoid false return spikes.
    ``total_return_pct`` and ``cagr_pct`` use ``total_invested`` when given.
    """
    if not curve:
        raise ValueError("curve_metrics needs at least one point")
    values = [p["value"] for p in curve]
    first = dt.date.fromisoformat(curve[0]["date"])
    last = dt.date.fromisoformat(curve[-1]["date"])
    years = max((last - first).days, 1) / 365.25
    invested = total_invested if total_invested is not None else capital

    inflows_by_str: dict[str, float] = {}
    if cash_inflows:
        for k, v in cash_inflows.items():
            date_str = k.isoformat() if isinstance(k, dt.date) else str(k)
            inflows_by_str[date_str] = float(v)

    rets = [
        (values[i] - inflows_by_str.get(curve[i]["date"], 0.0) - values[i - 1]) / values[i - 1]
        for i in range(1, len(values))
        if values[i - 1] != 0
    ]
    n = len(rets)
    if n > 1:
        mean = sum(rets) / n
        var = sum((r - mean) ** 2 for r in rets) / (n - 1)
        std = math.sqrt(var)
        sharpe = (mean / std) * math.sqrt(SESSIONS_PER_YEAR) if std > 0 else 0.0
    else:
        sharpe = 0.0

    peak = values[0]
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        if peak:
            max_dd = min(max_dd, v / peak - 1.0)

    net_profit = values[-1] - invested
    total_return_pct = 100 * (net_profit / invested) if invested > 0 else 0.0
    cagr_pct = (
        100 * ((values[-1] / invested) ** (1 / years) - 1)
        if invested > 0 and values[-1] > 0
        else 0.0
    )

    return {
        "final_value": values[-1],
        "total_return_pct": total_return_pct,
        "cagr_pct": cagr_pct,
        "max_drawdown_pct": 100 * max_dd,
        "sharpe": sharpe,
    }


def exposure_metrics(curve: Sequence[dict[str, Any]], traded_notional: float) -> dict[str, float]:
    """Turnover (traded notional / mean equity) and mean cash share of equity, in %."""
    if not curve:
        raise ValueError("exposure_metrics needs at least one point")
    mean_value = sum(p["value"] for p in curve) / len(curve)
    return {
        "turnover": traded_notional / mean_value,
        "avg_cash_pct": 100 * sum(p["cash"] / p["value"] for p in curve) / len(curve),
    }


def max_drawdown(curve: Sequence[dict[str, Any]]) -> float:
    """Maximum drawdown as a negative percentage (<= 0)."""
    if not curve:
        return 0.0
    values = [p["value"] for p in curve]
    peak = values[0]
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        if peak:
            max_dd = min(max_dd, v / peak - 1.0)
    return 100 * max_dd


def sharpe_ratio(curve: Sequence[dict[str, Any]], capital: float) -> float:
    """Sharpe ratio (annualized over 252 sessions, zero risk-free, sample std)."""
    if len(curve) < 2:
        return 0.0
    values = [p["value"] for p in curve]
    rets = [
        (values[i] - values[i - 1]) / values[i - 1]
        for i in range(1, len(values))
        if values[i - 1] != 0
    ]
    n = len(rets)
    if n > 1:
        mean = sum(rets) / n
        var = sum((r - mean) ** 2 for r in rets) / (n - 1)
        std = math.sqrt(var)
        return (mean / std) * math.sqrt(SESSIONS_PER_YEAR) if std > 0 else 0.0
    return 0.0
