"""Order sizing for equal-weight rebalances (desired quantities, sells, buys)."""

from __future__ import annotations

import math
from typing import Any

from honba_examples.ewr.model import TOLERANCE_PCT


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
    eligible = [s for s in active if qty.get(s, 0) > 0 or closes[s] * (1 + fee) <= tentative]
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
