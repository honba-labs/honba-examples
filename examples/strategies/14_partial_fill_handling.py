"""strategies/14_partial_fill_handling: order lifecycle events and partial fill accounting.

Demonstrates:
- Handling asynchronous order events (SUBMITTED, ACCEPTED, PARTIAL_FILL, FILLED, CANCELLED)
- Cumulative fill accounting and remaining quantity tracking
- Active order modification when market price drifts past a threshold

Run::

    python examples/strategies/14_partial_fill_handling.py
    python examples/strategies/14_partial_fill_handling.py --target-qty 50 --modify-threshold 3.0

Pitfalls::

    - In-flight fill race conditions: modifying an order while a partial fill is in-flight can lead
      to overfills if the cancel/replace request races against exchange execution matching engines.
    - Partial fill inventory risk: strategies that assume atomic fills can leave the book stranded
      with fractional unhedged positions when liquidity thins and remaining lots stay unfilled.
    - Wash trade and self-cross risk: aggressively modifying unfilled limit orders toward market
      can cross resting opposite-side orders belonging to the same entity, violating exchange rules.
    - Event ordering guarantees: in high-throughput market microstructure, order update events
      may arrive out of sequence across distributed socket connections; sequencing must use sequence IDs.
"""

from __future__ import annotations

import argparse
import sys
from typing import ClassVar, override

from honba.domain.bar import Bar
from honba.domain.order import OrderIntent, OrderSide, OrderType
from honba.domain.trade import Trade
from honba.strategies.base import Strategy

from honba_examples.cli import run_example
from honba_examples.replay_report import replay_report
from honba_examples.synthetic import trending_bars

__all__ = ["OrderLifecycleEvent", "PartialFillStrategy", "main", "run"]


class OrderLifecycleEvent:
    """Represents an order lifecycle transition."""

    event_type: str
    order_id: str
    symbol: str
    quantity: float
    price: float | None
    remaining_qty: float
    message: str

    def __init__(
        self,
        event_type: str,
        order_id: str,
        symbol: str,
        quantity: float,
        price: float | None = None,
        remaining_qty: float = 0.0,
        message: str = "",
    ) -> None:
        self.event_type = event_type
        self.order_id = order_id
        self.symbol = symbol
        self.quantity = quantity
        self.price = price
        self.remaining_qty = remaining_qty
        self.message = message

    def to_dict(self) -> dict[str, object]:
        return {
            "event_type": self.event_type,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "quantity": self.quantity,
            "price": self.price,
            "remaining_qty": self.remaining_qty,
            "message": self.message,
        }


class PartialFillStrategy(Strategy):
    """Tracks order lifecycle events, partial fills, and modifies unfilled orders."""

    name: ClassVar[str] = "partial_fill_handling"
    target_quantity: float
    modify_threshold: float
    events_log: list[dict[str, object]]
    active_order_id: str | None
    active_order_price: float | None
    active_order_qty: float
    filled_quantity: float
    remaining_quantity: float
    _order_counter: int
    _submitted_initial: bool

    def __init__(
        self,
        target_quantity: float = 20.0,
        modify_threshold: float = 2.0,
    ) -> None:
        super().__init__()
        self.target_quantity = target_quantity
        self.modify_threshold = modify_threshold

        # Order tracking state
        self.events_log = []
        self.active_order_id = None
        self.active_order_price = None
        self.active_order_qty = 0.0
        self.filled_quantity = 0.0
        self.remaining_quantity = target_quantity
        self._order_counter = 0
        self._submitted_initial = False

    def on_order_event(self, event: dict[str, object]) -> None:
        """Process order lifecycle events from the execution engine."""
        self.events_log.append(event)
        etype = str(event.get("event_type", ""))

        if etype == "ACCEPTED":
            oid = event.get("order_id")
            self.active_order_id = str(oid) if oid is not None else None
            self.active_order_qty = float(str(event.get("quantity", 0.0)))
            px = event.get("price")
            self.active_order_price = float(str(px)) if px is not None else None
        elif etype in ("PARTIAL_FILL", "FILLED"):
            filled_chunk = float(str(event.get("fill_qty", 0.0)))
            self.filled_quantity += filled_chunk
            self.remaining_quantity = max(0.0, self.target_quantity - self.filled_quantity)
            if etype == "FILLED" or self.remaining_quantity <= 0.0:
                self.active_order_id = None
                self.active_order_qty = 0.0
                self.active_order_price = None
            else:
                self.active_order_qty = self.remaining_quantity
        elif etype == "CANCELLED":
            self.active_order_id = None
            self.active_order_qty = 0.0
            self.active_order_price = None

    @override
    def on_fill(self, fill: Trade) -> None:
        """Hook called when a fill executes."""
        is_complete = (self.filled_quantity + fill.quantity) >= self.target_quantity
        evt: dict[str, object] = {
            "event_type": "FILLED" if is_complete else "PARTIAL_FILL",
            "order_id": fill.order_id or self.active_order_id or "order-1",
            "symbol": fill.instrument_id.symbol,
            "fill_qty": fill.quantity,
            "price": fill.price,
            "remaining_qty": max(
                0.0, self.target_quantity - (self.filled_quantity + fill.quantity)
            ),
            "cum_filled": self.filled_quantity + fill.quantity,
        }
        self.on_order_event(evt)

    @override
    def on_bar(self, bar: Bar) -> None:
        # Initial order placement: submit buy limit order for target quantity
        if not self._submitted_initial:
            self._order_counter += 1
            order_id = f"ord-{self._order_counter}"
            limit_price = round(bar.close * 0.995, 2)  # slightly below market
            self._submitted_initial = True

            # Emit submission event
            self.on_order_event(
                OrderLifecycleEvent(
                    event_type="SUBMITTED",
                    order_id=order_id,
                    symbol=bar.instrument_id.symbol,
                    quantity=self.target_quantity,
                    price=limit_price,
                    remaining_qty=self.target_quantity,
                    message="Initial limit buy order",
                ).to_dict()
            )

            # Emit accepted event
            self.on_order_event(
                OrderLifecycleEvent(
                    event_type="ACCEPTED",
                    order_id=order_id,
                    symbol=bar.instrument_id.symbol,
                    quantity=self.target_quantity,
                    price=limit_price,
                    remaining_qty=self.target_quantity,
                    message="Order acknowledged by exchange",
                ).to_dict()
            )

            # Submit actual engine intent
            self.submit(
                OrderIntent(
                    bar.instrument_id,
                    OrderSide.BUY,
                    self.target_quantity,
                    OrderType.LIMIT,
                    price=limit_price,
                )
            )
            return

        # Check if active order needs modification due to price moving away
        if (
            self.active_order_id is not None
            and self.active_order_price is not None
            and self.remaining_quantity > 0.0
        ):
            price_drift = abs(bar.close - self.active_order_price)
            if price_drift >= self.modify_threshold:
                new_price = round(bar.close, 2)
                self.modify_order(bar, new_price=new_price, new_qty=self.remaining_quantity)

    def modify_order(self, bar: Bar, new_price: float, new_qty: float) -> None:
        """Simulate order cancellation and replacement with updated terms."""
        old_id = self.active_order_id or "ord-unknown"

        # Cancel event for remaining portion
        self.on_order_event(
            OrderLifecycleEvent(
                event_type="CANCELLED",
                order_id=old_id,
                symbol=bar.instrument_id.symbol,
                quantity=self.active_order_qty,
                price=self.active_order_price,
                remaining_qty=self.remaining_quantity,
                message=f"Order {old_id} cancelled for replacement",
            ).to_dict()
        )

        # Replacement modified order
        self._order_counter += 1
        new_id = f"ord-{self._order_counter}"
        self.on_order_event(
            OrderLifecycleEvent(
                event_type="MODIFIED",
                order_id=new_id,
                symbol=bar.instrument_id.symbol,
                quantity=new_qty,
                price=new_price,
                remaining_qty=new_qty,
                message=f"Replaced {old_id} with {new_id} at price {new_price}",
            ).to_dict()
        )

        self.on_order_event(
            OrderLifecycleEvent(
                event_type="ACCEPTED",
                order_id=new_id,
                symbol=bar.instrument_id.symbol,
                quantity=new_qty,
                price=new_price,
                remaining_qty=new_qty,
                message=f"Order {new_id} accepted",
            ).to_dict()
        )

        self.submit(
            OrderIntent(
                bar.instrument_id,
                OrderSide.BUY,
                new_qty,
                OrderType.LIMIT,
                price=new_price,
            )
        )


def run(
    bars: int = 100,
    target_quantity: float = 20.0,
    modify_threshold: float = 2.0,
) -> dict[str, object]:
    bars_list = trending_bars("RELIANCE", bars)
    strat = PartialFillStrategy(target_quantity=target_quantity, modify_threshold=modify_threshold)
    report = replay_report(
        strat,
        bars_list,
        {
            "target_quantity": target_quantity,
            "modify_threshold": modify_threshold,
        },
        config_extra={"bars": len(bars_list)},
    )
    res_dict = report.get("result", {})
    if isinstance(res_dict, dict):
        res_dict["order_events"] = strat.events_log
        res_dict["filled_quantity"] = strat.filled_quantity
        res_dict["remaining_quantity"] = strat.remaining_quantity
    return report


def _add_args(parser: argparse.ArgumentParser) -> None:
    _ = parser.add_argument("--bars", type=int, default=100, help="number of synthetic bars")
    _ = parser.add_argument(
        "--target-qty", type=float, default=20.0, dest="target_quantity", help="target quantity"
    )
    _ = parser.add_argument(
        "--modify-threshold",
        type=float,
        default=2.0,
        help="price move threshold to trigger order modification",
    )


def main(argv: list[str] | None = None) -> int:
    return run_example(run, __doc__, _add_args, argv=argv)


if __name__ == "__main__":
    sys.exit(main())
