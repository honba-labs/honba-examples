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
    print_changes,
    print_data_notes,
    print_equity_summary,
    print_holdings,
    print_metrics,
)

STARTING_CAPITAL = 1_000_000.0
FEE_RATE = 0.001
REBALANCE_DAYS = 15


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


def rebalance_orders(
    qty: dict[str, int],
    closes: dict[str, float],
    cash: float,
    fee: float,
) -> tuple[float, list[dict[str, Any]]]:
    """Bring a basket back to equal weight against one shared cash pool.

    ``qty`` is mutated in place to the new positions.
    ``closes`` holds only the symbols that printed this session. A symbol held but
    absent from ``closes`` is left untouched and excluded from both the pool and the
    target. A symbol in ``closes`` but not yet held counts as 0 shares.

    Sells run before buys so the cash they raise funds the buys, whole shares only,
    largest rupee shortfall first. A buy is capped at what cash allows once the fee
    is included.
    """
    active = sorted(closes)
    if not active:
        return cash, []

    for s in active:
        qty.setdefault(s, 0)

    pool = cash + sum(qty[s] * closes[s] for s in active)
    target = pool / len(active)
    desired = {s: math.floor(target / closes[s]) for s in active}

    trades: list[dict[str, Any]] = []
    for s in active:
        sells, cash = _sell_excess(s, qty, desired, closes, cash, fee)
        trades.extend(sells)

    for s in active:
        buys, cash = _buy_shortfall(s, qty, desired, closes, cash, fee)
        trades.extend(buys)

    return max(cash, 0.0), trades


def sell_excess(
    qty: dict[str, int],
    closes: dict[str, float],
    cash: float,
    fee: float,
) -> tuple[float, list[dict[str, Any]]]:
    """Sell-only leg: trim every holding that sits above its equal-weight target."""
    active = sorted(closes)
    if not active:
        return cash, []
    pool = cash + sum(qty.get(s, 0) * closes[s] for s in active)
    desired = {s: math.floor((pool / len(active)) / closes[s]) for s in active}

    trades: list[dict[str, Any]] = []
    for s in active:
        sells, cash = _sell_excess(s, qty, desired, closes, cash, fee)
        trades.extend(sells)
    return max(cash, 0.0), trades


def buy_shortfall(
    qty: dict[str, int],
    closes: dict[str, float],
    cash: float,
    fee: float,
) -> tuple[float, list[dict[str, Any]]]:
    """Buy-only leg: top up holdings below target from cash that is already usable."""
    active = sorted(closes)
    if not active:
        return cash, []
    pool = cash + sum(qty.get(s, 0) * closes[s] for s in active)
    desired = {s: math.floor((pool / len(active)) / closes[s]) for s in active}

    trades: list[dict[str, Any]] = []
    shortfalls = sorted(
        (s for s in active if desired[s] > qty.get(s, 0)),
        key=lambda s: (-(desired[s] - qty.get(s, 0)) * closes[s], s),
    )
    for s in shortfalls:
        buys, cash = _buy_shortfall(s, qty, desired, closes, cash, fee)
        trades.extend(buys)
    return max(cash, 0.0), trades


def _sell_excess(
    s: str,
    qty: dict[str, int],
    desired: dict[str, int],
    closes: dict[str, float],
    cash: float,
    fee: float,
) -> tuple[list[dict[str, Any]], float]:
    held = qty.get(s, 0)
    if held <= desired[s]:
        return [], cash
    n = held - desired[s]
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
) -> tuple[list[dict[str, Any]], float]:
    held = qty.get(s, 0)
    if desired[s] <= held:
        return [], cash
    affordable = math.floor(cash / (closes[s] * (1 + fee)))
    n = min(desired[s] - held, affordable)
    if n <= 0:
        return [], cash
    notional = n * closes[s]
    qty[s] = held + n
    return [_trade(s, "buy", n, closes[s], fee)], cash - notional - notional * fee


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
) -> SimResult:
    """Run the equal-weight rebalance simulation over bars.

    ``settlement_days`` selects the execution model:
    * ``0``: same-session model (sells and buys execute in the same session).
    * ``1`` or ``2``: delivery settlement cycle (the rebalance session sells only,
      and the buy leg runs ``settlement_days`` sessions later when proceeds settle).
    """
    if capital <= 0:
        raise ValueError("capital must be positive")
    if rebalance_days < 1:
        raise ValueError("rebalance_days must be at least 1")
    if settlement_days < 0:
        raise ValueError("settlement_days must be >= 0")
    if not 0 <= fee < 1:
        raise ValueError("fee must be in [0, 1)")

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
    result = SimResult()
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
            cash, buys = buy_shortfall(qty, today, cash, fee)
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
                cash, trades = sell_excess(qty, today, cash, fee)
                pending_buys[idx + settlement_days] = True
                leg = "sell"
            else:
                cash, trades = rebalance_orders(qty, today, cash, fee)
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
        value = cash + sum(q * last_close[s] for s, q in qty.items() if q)
        result.equity_curve.append({"date": session.isoformat(), "value": value, "cash": cash})

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
    result.final_holdings = {s: q for s, q in qty.items() if q}
    result.never_held = [s for s in sorted(symbols) if s not in ever_held and s in first_price]
    return result


def print_summary(result: SimResult, opts: OutputOptions | None = None) -> None:
    """Print the simulation summary through the shared example output presets."""
    opts = opts or OutputOptions()
    metrics = dict(result.metrics)
    metrics["traded_notional"] = sum(t["notional"] for r in result.rebalances for t in r["trades"])
    print_metrics(metrics, opts)
    print_changes(result.rebalances, opts)
    print_holdings(result.final_holdings, opts)
    print_equity_summary(result.equity_curve, opts)
    print_data_notes(opts, missing_data=result.missing_data, never_held=result.never_held)
