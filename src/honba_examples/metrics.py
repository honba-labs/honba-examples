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

__all__ = [
    "SESSIONS_PER_YEAR",
    "curve_metrics",
    "exposure_metrics",
    "fills_equity_curve",
    "max_drawdown",
    "sharpe_ratio",
    "sortino_ratio",
]

SESSIONS_PER_YEAR = 252


def _xirr(
    cash_flows: Sequence[tuple[dt.date, float]], tol: float = 1e-6, max_iter: int = 100
) -> float | None:
    """Annualized money-weighted return (XIRR) via Newton-Raphson with a bisection fallback.

    ``cash_flows``: ``(date, amount)`` pairs, outflows negative and the terminal value
    positive. Returns the rate as a percentage, or ``None`` if it can't be solved. A separate
    copy of the same algorithm as :func:`honba_examples.sip_plan.calculate_xirr`, kept local so
    this lightweight, dependency-free module doesn't import that heavier one.
    """
    if len(cash_flows) < 2:
        return None
    amounts = [a for _, a in cash_flows]
    if not (any(a < 0 for a in amounts) and any(a > 0 for a in amounts)):
        return None

    d0 = cash_flows[0][0]
    fractions = [(d - d0).days / 365.25 for d, _ in cash_flows]

    def xnpv(r: float) -> float:
        return sum(a / ((1.0 + r) ** t) for t, a in zip(fractions, amounts))

    def dxnpv(r: float) -> float:
        return sum(-t * a / ((1.0 + r) ** (t + 1.0)) for t, a in zip(fractions, amounts))

    r = 0.1
    for _ in range(max_iter):
        f = xnpv(r)
        df = dxnpv(r)
        if abs(df) < 1e-12:
            break
        r_next = r - f / df
        if abs(r_next - r) < tol:
            return r_next * 100.0
        r = r_next
        if r <= -0.99:
            r = -0.9

    low, high = -0.99, 5.0
    f_low, f_high = xnpv(low), xnpv(high)
    if f_low * f_high > 0:
        return None
    mid = (low + high) / 2.0
    for _ in range(max_iter):
        mid = (low + high) / 2.0
        f_mid = xnpv(mid)
        if abs(f_mid) < tol or (high - low) / 2.0 < tol:
            return mid * 100.0
        if f_low * f_mid < 0:
            high = mid
            f_high = f_mid
        else:
            low = mid
            f_low = f_mid
    return mid * 100.0


def fills_equity_curve(capital: float, fills: Sequence[Any], bars: Sequence[Any]) -> list[float]:
    """Equity at each bar close: ``capital`` first, then cash plus open position at that close.

    A fill is applied at its own price on the bar whose ``ts`` it carries, before that bar is
    marked, so a position held to the last bar shows its unrealised gain or loss.
    Single-instrument: every fill is taken to be in the instrument ``bars`` prices.
    """
    pending = sorted(fills, key=lambda f: f.ts)
    cash, position, i = capital, 0.0, 0
    curve = [capital]
    for b in bars:
        while i < len(pending) and pending[i].ts <= b.ts:
            f = pending[i]
            sign = 1.0 if f.side.value == "buy" else -1.0
            cash -= sign * f.quantity * f.price
            position += sign * f.quantity
            i += 1
        curve.append(cash + position * b.close)
    return curve


def curve_metrics(
    curve: Sequence[dict[str, Any]],
    capital: float,
    *,
    cash_inflows: Mapping[Any, float] | None = None,
    total_invested: float | None = None,
) -> dict[str, float]:
    """Final value, total return %, CAGR %, max drawdown % (<= 0) and Sharpe.

    When ``cash_inflows`` (e.g. SIP deposits) is provided, session returns use Time-Weighted
    Return (TWR) formulation: ``(V_t - Inflow_t - V_{t-1}) / V_{t-1}`` to avoid false return spikes.
    ``total_return_pct`` and ``cagr_pct`` use ``total_invested`` when given, which makes
    ``cagr_pct`` a lump-sum-equivalent rate (as if ``total_invested`` sat at ``capital`` from day
    one) rather than a true annualized return: with staggered inflows it reads low. The result
    then also carries ``xirr_pct``, the money-weighted annualized return (Newton-Raphson XIRR
    over ``capital`` at day one, each inflow, and the final value) that actually reconciles with
    ``total_return_pct``.
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

    out = {
        "final_value": values[-1],
        "total_return_pct": total_return_pct,
        "cagr_pct": cagr_pct,
        "max_drawdown_pct": 100 * max_dd,
        "sharpe": sharpe,
    }
    if cash_inflows:
        flows = [(first, -capital)]
        flows += [
            (k if isinstance(k, dt.date) else dt.date.fromisoformat(str(k)), -float(v))
            for k, v in cash_inflows.items()
        ]
        flows.append((last, values[-1]))
        flows.sort(key=lambda f: f[0])
        xirr_pct = _xirr(flows)
        if xirr_pct is not None:
            out["xirr_pct"] = xirr_pct
    return out


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


def sortino_ratio(curve: Sequence[dict[str, Any]], capital: float) -> float:
    """Sortino ratio (annualized over 252 sessions, zero risk-free target).

    Like :func:`sharpe_ratio` but the denominator is the downside deviation (sample std of
    negative session returns only). A curve with no downside returns reports 0.0, mirroring
    :func:`sharpe_ratio` when there is no variance.
    """
    if len(curve) < 2:
        return 0.0
    values = [p["value"] for p in curve]
    rets = [
        (values[i] - values[i - 1]) / values[i - 1]
        for i in range(1, len(values))
        if values[i - 1] != 0
    ]
    downside = [r for r in rets if r < 0]
    n = len(downside)
    if n == 0:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum(r * r for r in downside) / (n - 1) if n > 1 else 0.0
    std = math.sqrt(var)
    return (mean / std) * math.sqrt(SESSIONS_PER_YEAR) if std > 0 else 0.0
