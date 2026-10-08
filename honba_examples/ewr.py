"""Equal-weight rebalancing simulation engine.

Provides simulation and order sizing utilities to maintain equal weights across a basket
against a single shared cash pool, with support for cash-settlement cycles (e.g. T+1, T+2).
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any

from honba.domain.bar import Bar

from honba_examples.base import ts_to_date
from honba_examples.metrics import curve_metrics, exposure_metrics
from honba_examples.output import (
    OutputOptions,
    print_data_notes,
    print_equity_summary,
    print_metrics,
    print_rebalance_schedule,
)

STARTING_CAPITAL = 1_000_000.0
FEE_RATE = 0.001
REBALANCE_DAYS = 15
TOLERANCE_PCT = 0.05
DEFAULT_LEVERAGE = 2.0
DEFAULT_INITIAL_MARGIN = 0.5
DEFAULT_MAINTENANCE_MARGIN = 0.25


@dataclass
class SimResult:
    """Outcome of one equal-weight rebalance simulation."""

    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    rebalances: list[dict[str, Any]] = field(default_factory=list)
    rebalance_changes: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    final_holdings: dict[str, int] = field(default_factory=dict)
    never_held: list[str] = field(default_factory=list)
    missing_data: list[str] = field(default_factory=list)
    margin_used_curve: list[float] = field(default_factory=list)
    max_margin_used_pct: float = 0.0
    margin_calls: int = 0
    leverage: float = 1.0
    initial_margin: float = DEFAULT_INITIAL_MARGIN
    maintenance_margin: float = DEFAULT_MAINTENANCE_MARGIN


def _compute_desired(
    qty: dict[str, int],
    closes: dict[str, float],
    cash: float,
    fee: float,
    tolerance_pct: float,
    *,
    leverage: float = 1.0,
) -> tuple[float, list[str], dict[str, int]]:
    """Compute equal-weight target, eligible symbols, and desired share positions.

    * Unaffordable unheld stocks (single share > tentative target) are excluded from the
      target denominator to avoid locking capital into uninvestable idle cash.
    * Existing holdings within ``tolerance_pct`` of their target value are preserved to
      prevent excessive turnover on small market drifts.
    * Target portfolio size scales with ``leverage``.
    """
    active = sorted(closes)
    if not active:
        return 0.0, [], {}

    pool = cash + sum(qty.get(s, 0) * closes[s] for s in active)
    target_pool = max(0.0, pool) * leverage
    tentative = target_pool / len(active) if target_pool > 0 else 0.0
    eligible = [
        s for s in active if qty.get(s, 0) > 0 or closes[s] * (1 + fee) <= tentative
    ]
    if not eligible:
        eligible = list(active)
    target = target_pool / len(eligible) if target_pool > 0 else 0.0

    desired: dict[str, int] = {}
    for s in active:
        if s not in eligible:
            desired[s] = 0
            continue
        held = qty.get(s, 0)
        if held > 0 and tolerance_pct > 0 and target > 0:
            drift = abs(held * closes[s] - target) / target
            if drift <= tolerance_pct:
                desired[s] = held
                continue
        desired[s] = math.floor(target / closes[s]) if target > 0 else 0

    return target, eligible, desired


def _sweep_residual_cash(
    qty: dict[str, int],
    closes: dict[str, float],
    cash: float,
    fee: float,
    target: float,
    trades: list[dict[str, Any]],
    candidates: list[str],
) -> float:
    """Greedily allocate remaining cash to candidate holdings furthest below target."""
    if not candidates:
        return cash

    while True:
        affordable = [s for s in candidates if closes[s] * (1 + fee) <= cash]
        if not affordable:
            break
        best = min(affordable, key=lambda s: (qty[s] * closes[s] - target, closes[s]))
        cost = closes[best] * (1 + fee)
        if cost <= 0:
            break
        qty[best] += 1
        cash -= cost
        for t in trades:
            if t["symbol"] == best and t["side"] == "buy":
                t["qty"] += 1
                t["notional"] += closes[best]
                t["fee"] += closes[best] * fee
                break
        else:
            trades.append(_trade(best, "buy", 1, closes[best], fee))

    return cash


def rebalance_orders(
    qty: dict[str, int],
    closes: dict[str, float],
    cash: float,
    fee: float,
    *,
    tolerance_pct: float = TOLERANCE_PCT,
    sweep: bool = True,
    is_day0: bool = False,
    leverage: float = 1.0,
    allow_short: bool = True,
) -> tuple[float, list[dict[str, Any]]]:
    """Bring a basket back to equal weight against one shared cash pool.

    ``qty`` is mutated in place to the new positions.
    ``closes`` holds only the symbols that printed this session. A symbol held but
    absent from ``closes`` is left untouched and excluded from both the pool and the
    target. A symbol in ``closes`` but not yet held counts as 0 shares.

    Sells run before buys so the cash they raise funds the buys, whole shares only,
    largest rupee shortfall first. A buy is capped at what cash allows once the fee
    is included (or available margin borrowing capacity when leveraged).
    """
    active = sorted(closes)
    if not active:
        return cash, []

    for s in active:
        qty.setdefault(s, 0)

    target, eligible, desired = _compute_desired(
        qty, closes, cash, fee, tolerance_pct, leverage=leverage
    )

    trades: list[dict[str, Any]] = []
    for s in active:
        sells, cash = _sell_excess(s, qty, desired, closes, cash, fee, allow_short=allow_short)
        trades.extend(sells)

    equity = cash + sum(qty.get(s, 0) * closes[s] for s in active)
    max_borrow = max(0.0, equity * (leverage - 1.0)) if leverage > 1.0 else 0.0

    shortfalls = sorted(
        (s for s in active if desired[s] > qty.get(s, 0)),
        key=lambda s: (-(desired[s] - qty.get(s, 0)) * closes[s], s),
    )
    for s in shortfalls:
        buys, cash = _buy_shortfall(s, qty, desired, closes, cash, fee, max_borrow=max_borrow)
        trades.extend(buys)

    if sweep and cash > 0 and leverage <= 1.0:
        cands = list(eligible) if is_day0 else [t["symbol"] for t in trades if t["side"] == "buy"]
        cash = _sweep_residual_cash(qty, closes, cash, fee, target, trades, cands)

    return (max(cash, 0.0) if leverage <= 1.0 else cash), trades


def sell_excess(
    qty: dict[str, int],
    closes: dict[str, float],
    cash: float,
    fee: float,
    *,
    tolerance_pct: float = TOLERANCE_PCT,
    leverage: float = 1.0,
    allow_short: bool = True,
) -> tuple[float, list[dict[str, Any]]]:
    """Sell-only leg: trim every holding that sits above its equal-weight target."""
    active = sorted(closes)
    if not active:
        return cash, []

    _target, _eligible, desired = _compute_desired(
        qty, closes, cash, fee, tolerance_pct, leverage=leverage
    )

    trades: list[dict[str, Any]] = []
    for s in active:
        sells, cash = _sell_excess(s, qty, desired, closes, cash, fee, allow_short=allow_short)
        trades.extend(sells)
    return (max(cash, 0.0) if leverage <= 1.0 else cash), trades


def buy_shortfall(
    qty: dict[str, int],
    closes: dict[str, float],
    cash: float,
    fee: float,
    *,
    tolerance_pct: float = TOLERANCE_PCT,
    sweep: bool = True,
    is_day0: bool = False,
    leverage: float = 1.0,
) -> tuple[float, list[dict[str, Any]]]:
    """Buy-only leg: top up holdings below target from cash that is already usable."""
    active = sorted(closes)
    if not active:
        return cash, []

    target, eligible, desired = _compute_desired(
        qty, closes, cash, fee, tolerance_pct, leverage=leverage
    )

    equity = cash + sum(qty.get(s, 0) * closes[s] for s in active)
    max_borrow = max(0.0, equity * (leverage - 1.0)) if leverage > 1.0 else 0.0

    trades: list[dict[str, Any]] = []
    shortfalls = sorted(
        (s for s in active if desired[s] > qty.get(s, 0)),
        key=lambda s: (-(desired[s] - qty.get(s, 0)) * closes[s], s),
    )
    for s in shortfalls:
        buys, cash = _buy_shortfall(s, qty, desired, closes, cash, fee, max_borrow=max_borrow)
        trades.extend(buys)

    if sweep and cash > 0 and leverage <= 1.0:
        cands = list(eligible) if is_day0 else [t["symbol"] for t in trades if t["side"] == "buy"]
        cash = _sweep_residual_cash(qty, closes, cash, fee, target, trades, cands)

    return (max(cash, 0.0) if leverage <= 1.0 else cash), trades


def _sell_excess(
    s: str,
    qty: dict[str, int],
    desired: dict[str, int],
    closes: dict[str, float],
    cash: float,
    fee: float,
    *,
    allow_short: bool = True,
) -> tuple[list[dict[str, Any]], float]:
    held = qty.get(s, 0)
    if held <= desired[s]:
        return [], cash
    if not allow_short:
        n = max(0, min(held - desired[s], held))
    else:
        n = held - desired[s]
    if n <= 0:
        return [], cash
    notional = n * closes[s]
    qty[s] = held - n
    return [_trade(s, "sell", n, closes[s], fee)], cash + notional - notional * fee


def _buy_shortfall(
    s: str,
    qty: dict[str, int],
    desired: dict[str, int],
    closes: dict[str, float],
    cash: float,
    fee: float,
    *,
    max_borrow: float = 0.0,
    leverage: float = 1.0,
) -> tuple[list[dict[str, Any]], float]:
    held = qty.get(s, 0)
    if desired[s] <= held:
        return [], cash

    borrow_limit = max_borrow
    if borrow_limit <= 0 and leverage > 1.0:
        borrow_limit = max(0.0, cash * (leverage - 1.0))
    available = cash + borrow_limit
    if available <= 0:
        return [], cash

    affordable = math.floor(available / (closes[s] * (1 + fee)))
    n = min(desired[s] - held, affordable)
    if n <= 0:
        return [], cash
    notional = n * closes[s]
    qty[s] = held + n
    cost = notional + notional * fee
    return [_trade(s, "buy", n, closes[s], fee)], cash - cost


def _trade(symbol: str, side: str, n: int, price: float, fee: float) -> dict[str, Any]:
    notional = n * price
    return {
        "symbol": symbol,
        "side": side,
        "qty": n,
        "price": price,
        "notional": notional,
        "fee": notional * fee,
    }


def trade_changes(trades: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    """Net share change per symbol from fills: ``(adds, sells)``, each ``{symbol: qty}``."""
    net: dict[str, int] = {}
    for t in trades:
        signed = t["qty"] if t["side"] == "buy" else -t["qty"]
        net[t["symbol"]] = net.get(t["symbol"], 0) + signed
    adds = {s: q for s, q in sorted(net.items()) if q > 0}
    sells = {s: -q for s, q in sorted(net.items()) if q < 0}
    return adds, sells


def format_changes(adds: dict[str, int], sells: dict[str, int]) -> str:
    """``adds [AAA+3, ZED+10]  sells [MID-5]``: symbols sorted, ``[]`` when empty."""
    add_text = ", ".join(f"{s}+{q}" for s, q in sorted(adds.items()))
    sell_text = ", ".join(f"{s}-{q}" for s, q in sorted(sells.items()))
    return f"adds [{add_text}]  sells [{sell_text}]"


def rebalance_changes(rebalances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per rebalance, from its fills. A settlement-delayed buy leg is folded
    into the rebalance whose sell leg funded it."""
    groups: list[dict[str, Any]] = []
    for r in rebalances:
        if r.get("leg") == "buy" and groups:
            groups[-1]["trades"].extend(r["trades"])
        else:
            groups.append({"date": r["date"], "trades": list(r["trades"])})
    out = []
    for g in groups:
        adds, sells = trade_changes(g["trades"])
        out.append({"date": g["date"], "adds": adds, "sells": sells})
    return out


def rebalance_sessions(
    calendar: list[dt.date], start: dt.date, rebalance_days: int
) -> set[dt.date]:
    """Map each ``start + k*rebalance_days`` (k >= 1) to the first session on/after it."""
    sessions: set[dt.date] = set()
    scheduled = start + dt.timedelta(days=rebalance_days)
    i = 0
    while scheduled <= calendar[-1]:
        while calendar[i] < scheduled:
            i += 1
        if calendar[i] != calendar[0]:
            sessions.add(calendar[i])
        scheduled += dt.timedelta(days=rebalance_days)
    return sessions


def simulate(
    bars: list[Bar],
    symbols: list[str],
    *,
    start: dt.date,
    capital: float = STARTING_CAPITAL,
    rebalance_days: int = REBALANCE_DAYS,
    fee: float = FEE_RATE,
    settlement_days: int = 0,
    tolerance_pct: float = TOLERANCE_PCT,
    sweep: bool = True,
    leverage: float = 1.0,
    initial_margin: float = DEFAULT_INITIAL_MARGIN,
    maintenance_margin: float = DEFAULT_MAINTENANCE_MARGIN,
    allow_short: bool = False,
) -> SimResult:
    """Run the equal-weight rebalance simulation over bars.

    ``settlement_days`` selects the execution model:
    * ``0``: same-session model (sells and buys execute in the same session).
    * ``1`` or ``2``: delivery settlement cycle (the rebalance session sells only,
      and the buy leg runs ``settlement_days`` sessions later when proceeds settle).

    Margin parameters:
    * ``leverage``: multiplier for target basket sizing (default 1.0, e.g. 2.0).
    * ``initial_margin``: initial margin requirement (default 0.5).
    * ``maintenance_margin``: maintenance margin threshold for margin call check (default 0.25).
    * ``allow_short``: enable short-selling into negative positions (default False).
    """
    if capital <= 0:
        raise ValueError("capital must be positive")
    if rebalance_days < 1:
        raise ValueError("rebalance_days must be at least 1")
    if settlement_days < 0:
        raise ValueError("settlement_days must be >= 0")
    if not 0 <= fee < 1:
        raise ValueError("fee must be in [0, 1)")
    if tolerance_pct < 0:
        raise ValueError("tolerance_pct must be >= 0")
    if leverage <= 0:
        raise ValueError("leverage must be positive")
    if not 0 < initial_margin <= 1:
        raise ValueError("initial_margin must be in (0, 1]")
    if not 0 < maintenance_margin <= 1:
        raise ValueError("maintenance_margin must be in (0, 1]")

    wanted = set(symbols)
    closes: dict[str, dict[dt.date, float]] = {s: {} for s in wanted}
    for bar in bars:
        sym = bar.instrument_id.symbol
        if sym not in wanted:
            continue
        day = ts_to_date(bar.ts)
        if day >= start:
            closes[sym][day] = float(bar.close)

    symbols = [s for s in symbols if closes[s]]
    result = SimResult(
        leverage=leverage,
        initial_margin=initial_margin,
        maintenance_margin=maintenance_margin,
    )
    if not symbols:
        result.missing_data = [s for s in symbols if not closes[s]]
        return result

    calendar = sorted({d for by_date in closes.values() for d in by_date})
    day0 = calendar[0]
    sessions = rebalance_sessions(calendar, start, rebalance_days)

    qty: dict[str, int] = {s: 0 for s in symbols}
    last_close: dict[str, float] = {}
    first_price: dict[str, float] = {}
    cash = float(capital)
    ever_held: set[str] = set()

    pending_buys: dict[int, bool] = {}

    for idx, session in enumerate(calendar):
        today = {s: closes[s][session] for s in symbols if session in closes[s]}
        last_close.update(today)
        for s, p in today.items():
            first_price.setdefault(s, p)

        if settlement_days and pending_buys.pop(idx - settlement_days, False) and today:
            cash, buys = buy_shortfall(
                qty,
                today,
                cash,
                fee,
                tolerance_pct=tolerance_pct,
                sweep=sweep,
                leverage=leverage,
            )
            if buys:
                result.rebalances.append(
                    {
                        "date": session.isoformat(),
                        "value_before": None,
                        "trades": buys,
                        "cash_after": cash,
                        "leg": "buy",
                    }
                )

        if session == day0 or session in sessions:
            value_before = cash + sum(qty[s] * last_close[s] for s in qty if s in last_close)
            if settlement_days and session != day0:
                cash, trades = sell_excess(
                    qty,
                    today,
                    cash,
                    fee,
                    tolerance_pct=tolerance_pct,
                    leverage=leverage,
                    allow_short=allow_short,
                )
                pending_buys[idx + settlement_days] = True
                leg = "sell"
            else:
                cash, trades = rebalance_orders(
                    qty,
                    today,
                    cash,
                    fee,
                    tolerance_pct=tolerance_pct,
                    sweep=sweep,
                    is_day0=(session == day0),
                    leverage=leverage,
                    allow_short=allow_short,
                )
                leg = "both"
            result.rebalances.append(
                {
                    "date": session.isoformat(),
                    "value_before": value_before,
                    "trades": trades,
                    "cash_after": cash,
                    "leg": leg,
                }
            )

        ever_held.update(s for s, q in qty.items() if q > 0)
        value = cash + sum(q * last_close[s] for s, q in qty.items() if s in last_close)
        result.equity_curve.append({"date": session.isoformat(), "value": value, "cash": cash})

        # --- Margin calculations & Margin call check ---
        long_val = sum(q * last_close[s] for s, q in qty.items() if s in last_close and q > 0)
        short_val = sum(abs(q) * last_close[s] for s, q in qty.items() if s in last_close and q < 0)
        gross_exposure = long_val + short_val

        margin_used = max(0.0, -cash) + short_val * initial_margin
        result.margin_used_curve.append(margin_used)

        margin_used_pct = (margin_used / value * 100.0) if value > 0 else 100.0
        result.max_margin_used_pct = max(margin_used_pct, result.max_margin_used_pct)

        maint_req = gross_exposure * maintenance_margin
        if gross_exposure > 0 and value < maint_req:
            result.margin_calls += 1

    result.rebalance_changes = rebalance_changes(result.rebalances)
    traded_notional = sum(t["notional"] for r in result.rebalances for t in r["trades"])
    total_fees = sum(t["fee"] for r in result.rebalances for t in r["trades"])

    n_rebalances = len({r["date"] for r in result.rebalances if r.get("leg") != "buy"}) - 1
    exposure = exposure_metrics(result.equity_curve, traded_notional)
    result.metrics = {
        **curve_metrics(result.equity_curve, capital),
        "n_rebalances": n_rebalances,
        "total_fees": total_fees,
        "turnover": exposure["turnover"],
        "avg_cash_pct": exposure["avg_cash_pct"],
        "n_fills": sum(len(r["trades"]) for r in result.rebalances),
    }
    if leverage > 1.0 or result.margin_calls > 0 or result.max_margin_used_pct > 0:
        result.metrics["max_margin_used_pct"] = result.max_margin_used_pct
        result.metrics["margin_calls"] = float(result.margin_calls)

    result.final_holdings = {s: q for s, q in qty.items() if q}
    result.never_held = [s for s in sorted(symbols) if s not in ever_held and s in first_price]
    return result


def print_summary(result: SimResult, opts: OutputOptions | None = None) -> None:
    """Print the simulation summary through the shared example output presets."""
    opts = opts or OutputOptions()
    metrics = dict(result.metrics)
    metrics["traded_notional"] = sum(t["notional"] for r in result.rebalances for t in r["trades"])
    print_rebalance_schedule(result.rebalances, opts)
    print_metrics(metrics, opts)
    print_equity_summary(result.equity_curve, opts)
    print_data_notes(opts, missing_data=result.missing_data, never_held=result.never_held)
