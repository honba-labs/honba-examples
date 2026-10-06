"""Daily multi-instrument backtest loop over the honba core simulator.

``run_portfolio_backtest`` drives one core ``Strategy`` through the core
``StrategyRunner`` and ``LedgerContext`` with the core
``honba.backtest.simulated.NextOpenExecution`` as the execution port (built by
``make_simulator``: next-open fills, NSE delivery costs, date-aware settlement).
Bars are ordered into sessions by ``group_sessions``; per session:

1. the port opens the session: due sale proceeds settle, then orders from earlier
   sessions fill at this session's opens (sells first);
2. the fills are booked in the ledger and every bar of the session goes to the
   strategy;
3. a test-window session is marked to its closes and appended to the curve.

Sessions before ``test_start`` are warm-up: the strategy sees their bars (so
indicators converge) but the runner's warm-up gate (``warmup_bars`` = the number of
warm-up sessions) suppresses every order, so warm-up can neither trade nor leak
fills, fees or turnover into the result. A strategy that keeps a schedule (for
example "rebalance every 15 sessions") still advances that schedule during warm-up;
give such strategies no more warm-up than their indicators need.

Money is integer minor units of INR (paise, ADR 0011); floats are statistics only.
A session is one distinct bar timestamp, so a calendar gap shortens a T+N cycle
counted in sessions.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from honba.backtest.simulated import (
    FillCostFn,
    NextOpenExecution,
    SessionOpen,
    group_sessions,
    make_simulator,
)
from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.domain.money import Currency, Money
from honba.domain.order import OrderIntent, OrderSide
from honba.markets.india.costs import nse_equity_delivery_fill_cost
from honba.strategies.base import Strategy
from honba.strategies.context import LedgerContext
from honba.strategies.runner import StrategyRunner

from honba_examples.base import ts_to_date
from honba_examples.metrics import curve_metrics, exposure_metrics

__all__ = [
    "BacktestRun",
    "CanonicalOrderLedger",
    "FillRecord",
    "OrderEvent",
    "canonical_hash",
    "run_portfolio_backtest",
]

INR = Currency.INR

_REJECTION_KIND = {
    "insufficient_funds": "released_unfunded",
    "no_position": "released_no_position",
}


@dataclass(frozen=True, slots=True)
class FillRecord:
    """One fill as the ledger booked it (money in minor units)."""

    date: str
    order_id: str
    symbol: str
    exchange: str
    side: str
    quantity: float
    price: float
    notional_minor: int
    cost_minor: int
    submitted_date: str


@dataclass(frozen=True, slots=True)
class OrderEvent:
    """An order (or part of one) that did not fill, and why."""

    date: str
    order_id: str
    symbol: str
    side: str
    quantity: float
    kind: str  # "suppressed_warmup" | "released_unfunded" | "released_no_position" | ...


def _major(minor: int) -> float:
    return Money.from_minor(minor, INR).to_major()


class CanonicalOrderLedger(LedgerContext):
    """``LedgerContext`` that hands the runner one event's intents in a canonical order.

    A strategy that loops over a ``set`` (Alpha-30 does) emits the same intents in an
    order that depends on ``PYTHONHASHSEED``, which would change order ids, fill order
    and, when cash is short, which buy gets cut. Intents raised by one event are one
    decision, so ordering them (sells first, then by instrument) changes no meaning
    and makes every run reproducible.
    """

    def drain_intents(self) -> list[OrderIntent]:
        return sorted(
            super().drain_intents(),
            key=lambda i: (
                i.side is not OrderSide.SELL,
                i.instrument_id.symbol,
                i.instrument_id.exchange,
            ),
        )


def canonical_hash(payload: Any) -> str:
    """sha256 of the canonical (sorted-key, compact) JSON form of ``payload``."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class BacktestRun:
    """Outcome of one run. Money fields are integer minor units; floats are statistics only."""

    capital_minor: int
    settlement_days: int
    fills: list[FillRecord] = field(default_factory=list)
    order_events: list[OrderEvent] = field(default_factory=list)
    curve: list[dict[str, Any]] = field(default_factory=list)
    final_positions: dict[str, float] = field(default_factory=dict)
    fees_minor: int = 0
    traded_notional_minor: int = 0
    warmup_sessions: int = 0
    test_sessions: int = 0
    bars_seen: int = 0
    rejections: int = 0

    def float_curve(self) -> list[dict[str, Any]]:
        """The curve in rupees (floats) for the statistics in ``honba_examples.metrics``."""
        return [
            {
                "date": p["date"],
                "value": _major(p["equity_minor"]),
                "cash": _major(p["cash_minor"]),
            }
            for p in self.curve
        ]

    def metrics(self) -> dict[str, Any]:
        curve = self.float_curve()
        capital = _major(self.capital_minor)
        kinds = [e.kind for e in self.order_events]
        last = self.curve[-1]
        return {
            **curve_metrics(curve, capital),
            **exposure_metrics(curve, _major(self.traded_notional_minor)),
            "final_equity_minor": last["equity_minor"],
            "final_cash_minor": last["cash_minor"],
            "positions_value_minor": last["positions_value_minor"],
            "total_fees_minor": self.fees_minor,
            "total_fees": _major(self.fees_minor),
            "traded_notional_minor": self.traded_notional_minor,
            "n_fills": len(self.fills),
            "n_suppressed_warmup": kinds.count("suppressed_warmup"),
            "n_released_unfunded": kinds.count("released_unfunded"),
            "n_released_no_position": kinds.count("released_no_position"),
            "n_unfilled_at_end": kinds.count("unfilled_at_end"),
            "n_rejected_intents": self.rejections,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "capital_minor": self.capital_minor,
            "settlement_days": self.settlement_days,
            "warmup_sessions": self.warmup_sessions,
            "test_sessions": self.test_sessions,
            "bars_seen": self.bars_seen,
            "metrics": self.metrics(),
            "final_positions": dict(sorted(self.final_positions.items())),
            "fills": [asdict(f) for f in self.fills],
            "order_events": [asdict(e) for e in self.order_events],
            "equity_curve": self.curve,
        }


def run_portfolio_backtest(
    strategy: Strategy,
    bars: Sequence[Bar],
    *,
    test_start: dt.date,
    test_end: dt.date,
    capital_minor: int,
    settlement_days: int | None = None,
    costs: FillCostFn = nse_equity_delivery_fill_cost,
    exchange: str = "NSE",
) -> BacktestRun:
    """Run ``strategy`` over ``bars``; trade and account only within ``[test_start, test_end]``.

    ``settlement_days=None`` takes the core market-pack cycle for ``exchange`` as of
    ``test_start`` (NSE: T+2 before 2023-01-27, T+1 from then); an explicit value wins.
    """
    if test_start > test_end:
        raise ValueError(f"test_start {test_start} is after test_end {test_end}")
    kept = [b for b in bars if ts_to_date(b.ts) <= test_end]
    events = group_sessions(kept)
    session_days = [ts_to_date(e.ts) for e, _ in events if isinstance(e, SessionOpen)]
    if not any(day >= test_start for day in session_days):
        raise ValueError(f"no bars between {test_start} and {test_end}")
    n_warmup = sum(1 for day in session_days if day < test_start)

    port = make_simulator(
        fill="next_open",
        cash=Money.from_minor(capital_minor, INR),
        costs=costs,
        exchange=exchange,
        settlement_days=settlement_days,
        as_of=test_start,
    )
    assert isinstance(port, NextOpenExecution)
    ctx = CanonicalOrderLedger(cash=Money.from_minor(capital_minor, INR))
    runner = StrategyRunner(strategy, port, ctx=ctx, warmup_bars=n_warmup)
    run = BacktestRun(capital_minor=capital_minor, settlement_days=port.settlement_days)
    closes: dict[InstrumentId, float] = {}
    day: dt.date | None = None

    def mark() -> None:
        if day is None or day < test_start:
            return
        positions_value = Money.zero(INR)
        for iid, qty in port.positions.items():
            positions_value = positions_value + Money.mul_qty(qty, closes[iid], INR)
        run.test_sessions += 1
        run.curve.append(
            {
                "date": day.isoformat(),
                "equity_minor": (port.cash + positions_value).amount,
                "cash_minor": port.cash.amount,
                "available_cash_minor": port.available_cash.amount,
                "positions_value_minor": positions_value.amount,
            }
        )

    runner.start()
    for event, ts in events:
        port.on_event(event, ts)
        runner.on_event(event, ts)
        if isinstance(event, SessionOpen):
            mark()
            day = ts_to_date(event.ts)
        elif isinstance(event, Bar):
            closes[event.instrument_id] = event.close
            run.bars_seen += 1
    mark()
    runner.stop()
    run.warmup_sessions = n_warmup

    submitted = {s.order_id: s for s in runner.intents}
    for f in runner.fills:
        notional = Money.mul_qty(f.quantity, f.price, INR).amount
        sub = submitted.get(f.order_id or "")
        run.fills.append(
            FillRecord(
                date=ts_to_date(f.ts).isoformat(),
                order_id=f.order_id or "",
                symbol=f.instrument_id.symbol,
                exchange=f.instrument_id.exchange,
                side=f.side.value,
                quantity=f.quantity,
                price=f.price,
                notional_minor=notional,
                cost_minor=f.costs.amount,
                submitted_date=ts_to_date(sub.ts_init).isoformat() if sub else "",
            )
        )
    timed: list[tuple[int, OrderEvent]] = [
        (s.ts_init, _event(s.ts_init, "", s.intent, s.intent.quantity, "suppressed_warmup"))
        for s in runner.suppressed
    ]
    timed += [
        (
            r.ts,
            _event(
                r.ts,
                r.order_id,
                r.intent,
                r.intent.quantity,
                "cancelled" if r.cancelled else _REJECTION_KIND.get(r.reason, r.reason),
            ),
        )
        for r in runner.order_rejections
    ]
    run.order_events = [e for _, e in sorted(timed, key=lambda t: t[0])]
    last_ts = events[-1][1]
    for order_id in port.working_orders:
        sub = submitted[order_id]
        run.order_events.append(
            _event(last_ts, order_id, sub.intent, sub.intent.quantity, "unfilled_at_end")
        )
    run.fees_minor = port.fees.amount
    run.traded_notional_minor = port.traded_notional.amount
    run.final_positions = {iid.symbol: qty for iid, qty in port.positions.items()}
    run.rejections = len(runner.rejections)
    return run


def _event(ts: int, order_id: str, intent: OrderIntent, qty: float, kind: str) -> OrderEvent:
    return OrderEvent(
        date=ts_to_date(ts).isoformat(),
        order_id=order_id,
        symbol=intent.instrument_id.symbol,
        side=intent.side.value,
        quantity=qty,
        kind=kind,
    )
