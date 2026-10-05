"""Simulated multi-instrument execution: next-session-open fills, paise cash, T+N settlement.

``NextOpenExecution`` implements the core ``ExecutionPort`` protocol
(``honba.strategies.runner``: ``submit`` / ``drain_fills``) for daily portfolio
backtests. The core does not ship a multi-instrument next-open simulator yet
(``honba.strategies.testing.BarCloseFills`` is single-price and fills at the
decision bar's close; ``honba.session`` falls back to it), so this stays a thin
adapter until it does.

Rules, all deterministic:

* **Timing.** An order submitted during session *d* fills at the *open* of the
  instrument's next bar in a later session. It never sees the close that
  produced it. If the instrument does not print, the order waits for its next bar.
* **Order within a session.** Sells fill before buys; each side in submission order.
* **Cash.** Integer paise (ADR 0011). A buy debits ``notional + cost`` at once. A
  sell credits ``notional - cost`` at once but the proceeds only become
  *available* ``settlement_days`` sessions later (India delivery: T+2).
* **Funding.** A buy that available cash cannot cover waits up to
  ``settlement_days`` sessions for sale proceeds to settle; then it is cut to the
  whole shares cash allows and the remainder is released back to the strategy
  (``release``), so the strategy no longer sees the instrument as busy.
* **Long only.** A sell is capped at the position held.
* **Gate.** While trading is disabled (warm-up), every submitted order is
  released immediately and recorded as ``suppressed_warmup``; nothing fills.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderSide
from honba.domain.trade import Trade

from honba_examples.money import delivery_cost_paise, notional_paise, paise_to_rupees

__all__ = ["CostFn", "FillRecord", "NextOpenExecution", "OrderEvent"]

CostFn = Callable[[OrderSide, float, float], int]
"""``(side, quantity, price) -> cost in paise`` for one fill."""


@dataclass(frozen=True, slots=True)
class FillRecord:
    """One fill as the ledger booked it (money in paise)."""

    date: str
    order_id: str
    symbol: str
    exchange: str
    side: str
    quantity: float
    price: float
    notional_paise: int
    cost_paise: int
    submitted_date: str


@dataclass(frozen=True, slots=True)
class OrderEvent:
    """An order (or part of one) that did not fill, and why."""

    date: str
    order_id: str
    symbol: str
    side: str
    quantity: float
    kind: str  # "suppressed_warmup" | "released_unfunded" | "released_no_position" | "unfilled_at_end"


@dataclass
class _Pending:
    order_id: str
    intent: OrderIntent
    session: int  # session index the order was submitted in
    submitted_date: dt.date | None
    first_try: int | None = None  # first session it was eligible and considered


class NextOpenExecution:
    """``ExecutionPort`` filling at the next session's open with paise cash and settlement."""

    def __init__(
        self,
        *,
        cash_paise: int,
        settlement_days: int,
        release: Callable[[OrderIntent], None],
        cost_paise: CostFn = delivery_cost_paise,
        trading: bool = True,
    ) -> None:
        if settlement_days < 0:
            raise ValueError("settlement_days must be >= 0")
        if cash_paise < 0:
            raise ValueError("cash_paise must be >= 0")
        self.settlement_days = settlement_days
        self.cash_paise = int(cash_paise)
        self.fees_paise = 0
        self.traded_notional_paise = 0
        self.positions: dict[InstrumentId, float] = {}
        self.fill_records: list[FillRecord] = []
        self.order_events: list[OrderEvent] = []
        self._release = release
        self._cost = cost_paise
        self._trading = trading
        self._session = -1
        self._day: dt.date | None = None
        self._pending: list[_Pending] = []
        self._receivables: list[tuple[int, int]] = []  # (settles at session, paise)
        self._fills: list[Trade] = []

    # -- gate --------------------------------------------------------------------
    @property
    def trading_enabled(self) -> bool:
        return self._trading

    def enable_trading(self) -> None:
        self._trading = True

    # -- cash --------------------------------------------------------------------
    @property
    def unsettled_paise(self) -> int:
        return sum(amount for due, amount in self._receivables if due > self._session)

    @property
    def available_cash_paise(self) -> int:
        """Cash that may fund a buy now: booked cash less unsettled sale proceeds."""
        return self.cash_paise - self.unsettled_paise

    # -- ExecutionPort -----------------------------------------------------------
    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        if not self._trading:
            self._event(order_id, intent, intent.quantity, "suppressed_warmup")
            self._release(intent)
            return
        self._pending.append(_Pending(order_id, intent, self._session, self._day))

    def drain_fills(self) -> list[Trade]:
        fills, self._fills = self._fills, []
        return fills

    # -- session driver ----------------------------------------------------------
    def open_session(self, day: dt.date, bars: Sequence[Bar]) -> None:
        """Start session ``day``: settle due proceeds, then fill eligible orders at the opens."""
        self._session += 1
        self._day = day
        self._receivables = [(due, amt) for due, amt in self._receivables if due > self._session]
        opens = {b.instrument_id: b for b in bars}
        eligible = [
            p
            for p in self._pending
            if p.session < self._session and p.intent.instrument_id in opens
        ]
        for p in [p for p in eligible if p.intent.side is OrderSide.SELL]:
            self._fill_sell(p, opens[p.intent.instrument_id])
        for p in [p for p in eligible if p.intent.side is OrderSide.BUY]:
            self._fill_buy(p, opens[p.intent.instrument_id])

    def finish(self) -> None:
        """End of data: report every order still waiting."""
        for p in self._pending:
            self._event(p.order_id, p.intent, p.intent.quantity, "unfilled_at_end")
        self._pending = []

    # -- internals ---------------------------------------------------------------
    def _fill_sell(self, p: _Pending, bar: Bar) -> None:
        self._pending.remove(p)
        held = self.positions.get(p.intent.instrument_id, 0.0)
        qty = min(p.intent.quantity, held)
        if qty < p.intent.quantity:
            self._release_part(p, p.intent.quantity - qty, "released_no_position")
        if qty <= 0:
            return
        notional = notional_paise(qty, bar.open)
        cost = self._cost(OrderSide.SELL, qty, bar.open)
        proceeds = notional - cost
        self.cash_paise += proceeds
        self._receivables.append((self._session + self.settlement_days, proceeds))
        self._book(p, bar, qty, notional, cost)

    def _fill_buy(self, p: _Pending, bar: Bar) -> None:
        if p.first_try is None:
            p.first_try = self._session
        px = bar.open
        want = p.intent.quantity
        available = self.available_cash_paise
        if self._buy_cost(want, px) <= available:
            qty = want
        elif self._session - p.first_try < self.settlement_days:
            return  # wait for pending sale proceeds to settle
        else:
            qty = self._affordable(want, px, available)
        self._pending.remove(p)
        if qty < want:
            self._release_part(p, want - qty, "released_unfunded")
        if qty <= 0:
            return
        notional = notional_paise(qty, px)
        cost = self._cost(OrderSide.BUY, qty, px)
        self.cash_paise -= notional + cost
        self._book(p, bar, qty, notional, cost)

    def _buy_cost(self, qty: float, px: float) -> int:
        return notional_paise(qty, px) + self._cost(OrderSide.BUY, qty, px)

    def _affordable(self, want: float, px: float, available: int) -> float:
        if px <= 0 or available <= 0:
            return 0
        qty = min(math.floor(want), available // notional_paise(1, px))
        while qty > 0 and self._buy_cost(qty, px) > available:
            qty -= 1
        return qty

    def _book(self, p: _Pending, bar: Bar, qty: float, notional: int, cost: int) -> None:
        iid = p.intent.instrument_id
        signed = qty if p.intent.side is OrderSide.BUY else -qty
        self.positions[iid] = self.positions.get(iid, 0.0) + signed
        if self.positions[iid] == 0:
            del self.positions[iid]
        self.fees_paise += cost
        self.traded_notional_paise += notional
        self._fills.append(
            Trade(
                iid,
                p.intent.side,
                qty,
                bar.open,
                bar.ts,
                p.order_id,
                # LedgerContext books costs as a float in rupees; the paise ledger above
                # is the authority, this is its exact two-decimal image.
                costs=paise_to_rupees(cost),
            )
        )
        self.fill_records.append(
            FillRecord(
                date=self._day.isoformat() if self._day else "",
                order_id=p.order_id,
                symbol=iid.symbol,
                exchange=iid.exchange,
                side=p.intent.side.value,
                quantity=qty,
                price=bar.open,
                notional_paise=notional,
                cost_paise=cost,
                submitted_date=p.submitted_date.isoformat() if p.submitted_date else "",
            )
        )

    def _release_part(self, p: _Pending, qty: float, kind: str) -> None:
        self._event(p.order_id, p.intent, qty, kind)
        self._release(dataclasses.replace(p.intent, quantity=qty))

    def _event(self, order_id: str, intent: OrderIntent, qty: float, kind: str) -> None:
        self.order_events.append(
            OrderEvent(
                date=self._day.isoformat() if self._day else "",
                order_id=order_id,
                symbol=intent.instrument_id.symbol,
                side=intent.side.value,
                quantity=qty,
                kind=kind,
            )
        )
