"""Equal-weight rebalancing simulation loop."""

from __future__ import annotations

import datetime as dt

from honba.domain.bar import Bar

from honba_examples.base import ts_to_date
from honba_examples.ewr.model import (
    DEFAULT_INITIAL_MARGIN,
    DEFAULT_MAINTENANCE_MARGIN,
    FEE_RATE,
    REBALANCE_DAYS,
    STARTING_CAPITAL,
    TOLERANCE_PCT,
    SimResult,
)
from honba_examples.ewr.reporting import rebalance_changes
from honba_examples.ewr.schedule import rebalance_sessions
from honba_examples.ewr.sizing import buy_shortfall, rebalance_orders, sell_excess
from honba_examples.metrics import curve_metrics, exposure_metrics

MARGIN_NOTE = (
    "Margin is tracked, not enforced: a margin call is only counted (the simulation never "
    "liquidates positions) and no interest is charged on borrowed cash."
)


def simulate(
    bars: list[Bar],
    symbols: list[str],
    *,
    start: dt.date,
    capital: float = STARTING_CAPITAL,
    rebalance_days: int = REBALANCE_DAYS,
    sip_amount: float = 0.0,
    no_of_sip: int | None = None,
    sip_duration_days: int | None = None,
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

    Margin is tracked, not enforced: ``margin_calls`` only counts sessions below the maintenance
    requirement; positions are never liquidated and no interest is charged on borrowed cash (see
    ``SimResult.notes``). Symbols with no bars on or after ``start`` are in ``missing_data``.

    SIP parameters:
    * ``sip_amount``: fixed rupee cash injected on each scheduled rebalance session after Day 0.
    * ``no_of_sip``: limit on the total count of SIP installments.
    * ``sip_duration_days``: calendar days limit from start for SIP installments.
    """
    if capital <= 0:
        raise ValueError("capital must be positive")
    if rebalance_days < 1:
        raise ValueError("rebalance_days must be at least 1")
    if sip_amount < 0:
        raise ValueError("sip_amount must be non-negative")
    if no_of_sip is not None and no_of_sip < 0:
        raise ValueError("no_of_sip must be non-negative")
    if sip_duration_days is not None and sip_duration_days < 0:
        raise ValueError("sip_duration_days must be non-negative")
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

    missing = [s for s in symbols if not closes[s]]  # asked for, but no bars on/after ``start``
    symbols = [s for s in symbols if closes[s]]
    result = SimResult(
        leverage=leverage,
        initial_margin=initial_margin,
        maintenance_margin=maintenance_margin,
    )
    result.missing_data = missing
    if not symbols:
        return result

    calendar = sorted({d for by_date in closes.values() for d in by_date})
    day0 = calendar[0]
    sessions = rebalance_sessions(calendar, start, rebalance_days)
    max_sessions = len(sessions)

    if sip_duration_days is not None:
        if sip_duration_days < rebalance_days:
            raise ValueError(
                f"SIP duration ({sip_duration_days} days) is less than rebalance_days ({rebalance_days}); "
                f"no SIP installments would occur."
            )
        duration_cutoff = start + dt.timedelta(days=sip_duration_days)
        if duration_cutoff > calendar[-1]:
            raise ValueError(
                f"SIP duration of {sip_duration_days} days (until {duration_cutoff}) exceeds available "
                f"calendar end date ({calendar[-1]}). Maximum possible duration for this data is "
                f"{(calendar[-1] - start).days} days."
            )
        derived_max_sips = sip_duration_days // rebalance_days
        if no_of_sip is not None and no_of_sip > derived_max_sips:
            raise ValueError(
                f"Requested SIP count ({no_of_sip}) exceeds the {derived_max_sips} installments "
                f"possible within duration ({sip_duration_days} days) for rebalance_days={rebalance_days}."
            )
        if no_of_sip is None:
            no_of_sip = derived_max_sips

    if no_of_sip is not None and no_of_sip > max_sessions:
        raise ValueError(
            f"Requested SIP count ({no_of_sip}) exceeds the {max_sessions} available rebalance sessions "
            f"for rebalance_days={rebalance_days} between {start} and {calendar[-1]}. "
            f"Either reduce --no-of-sip <= {max_sessions} or decrease --rebalance-days."
        )

    qty: dict[str, int] = {s: 0 for s in symbols}
    last_close: dict[str, float] = {}
    first_price: dict[str, float] = {}
    cash = float(capital)
    ever_held: set[str] = set()

    pending_buys: dict[int, bool] = {}

    sips_executed = 0
    total_invested = float(capital)
    sip_cutoff_date = (
        (start + dt.timedelta(days=sip_duration_days)) if sip_duration_days is not None else None
    )
    cash_inflows: dict[dt.date, float] = {}

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
            sip_injected = 0.0
            if session != day0 and sip_amount > 0:
                count_ok = (no_of_sip is None) or (sips_executed < no_of_sip)
                duration_ok = (sip_cutoff_date is None) or (session <= sip_cutoff_date)
                if count_ok and duration_ok:
                    cash += sip_amount
                    total_invested += sip_amount
                    sips_executed += 1
                    sip_injected = sip_amount
                    cash_inflows[session] = sip_amount

            value_before = (cash - sip_injected) + sum(
                qty[s] * last_close[s] for s in qty if s in last_close
            )
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
                    "sip_injected": sip_injected,
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

    result.initial_corpus = float(capital)
    result.sip_amount = float(sip_amount)
    result.total_invested = float(total_invested)
    result.sips_executed = sips_executed

    result.rebalance_changes = rebalance_changes(result.rebalances)
    traded_notional = sum(t["notional"] for r in result.rebalances for t in r["trades"])
    total_fees = sum(t["fee"] for r in result.rebalances for t in r["trades"])

    n_rebalances = len({r["date"] for r in result.rebalances if r.get("leg") != "buy"}) - 1
    exposure = exposure_metrics(result.equity_curve, traded_notional)
    result.metrics = {
        **curve_metrics(
            result.equity_curve,
            capital,
            cash_inflows=cash_inflows,
            total_invested=total_invested,
        ),
        "n_rebalances": n_rebalances,
        "total_fees": total_fees,
        "turnover": exposure["turnover"],
        "avg_cash_pct": exposure["avg_cash_pct"],
        "n_fills": sum(len(r["trades"]) for r in result.rebalances),
    }
    if sips_executed > 0 or sip_amount > 0:
        result.metrics["initial_corpus"] = float(capital)
        result.metrics["total_invested"] = float(total_invested)
        result.metrics["sips_executed"] = float(sips_executed)
        result.metrics["net_profit"] = result.equity_curve[-1]["value"] - total_invested

    if leverage > 1.0 or result.margin_calls > 0 or result.max_margin_used_pct > 0:
        result.metrics["max_margin_used_pct"] = result.max_margin_used_pct
        result.metrics["margin_calls"] = float(result.margin_calls)
        result.notes.append(MARGIN_NOTE)

    result.final_holdings = {s: q for s, q in qty.items() if q}
    result.never_held = [s for s in sorted(symbols) if s not in ever_held and s in first_price]
    return result
