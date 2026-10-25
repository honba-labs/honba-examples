"""strategies/14_partial_fill_handling: order lifecycle events and partial fill handling.

Demonstrates:
- Order lifecycle tracking via ``on_order_event`` and ``on_fill``
- Cumulative filled vs remaining quantity accounting
- Order modification when market moves away from an unfilled remainder
- Safe cancellation and replacement of in-flight orders

Run::

    python examples/strategies/14_partial_fill_handling.py
    python examples/strategies/14_partial_fill_handling.py --target-qty 50.0

Pitfalls::

    - Adverse selection on partial fills: limit orders that only partially fill frequently suffer from
      adverse selection; they fill because aggressive order flow is blowing through the level, leaving
      the strategy holding an incomplete position against momentum.
    - Race conditions during modification (in-flight fills): when cancelling a partially filled order to
      modify the price, an exchange fill can cross in-flight with the cancel request, risking double-fill
      or unintended excess exposure if not handled idempotently.
    - Loss of exchange queue priority: modifying a limit order's price or increasing its quantity resets
      its priority to the back of the matching engine's FIFO queue at that price level.
    - Lot size and tick size constraints: modifying the remaining quantity of an order must strictly respect
      exchange minimum lot sizes and lot multiples; fractional or odd-lot remainders can be rejected by RMS.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from honba.domain.bar import Bar
from honba.domain.order import OrderIntent, OrderSide, OrderType
from honba.strategies.base import Strategy

from honba_examples.cli import run_example
from honba_examples.replay_report import replay_report
from honba_examples.synthetic import trending_bars

__all__ = ["OrderLifecycleEvent", "PartialFillStrategy", "main", "run"]


class OrderLifecycleEvent:
    """Represents an order lifecycle transition."""

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

    def to_dict(self) -> dict[str, Any]:
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

    name = "partial_fill_handling"

    def __init__(
        self,
        target_quantity: float = 20.0,
        modify_threshold: float = 2.0,
    ) -> None:
        super().__init__()
        self.target_quantity = target_quantity
        self.modify_threshold = modify_threshold

        # Order tracking state
        self.events_log: list[dict[str, Any]] = []
        self.active_order_id: str | None = None
        self.active_order_price: float | None = None
        self.active_order_qty: float = 0.0
        self.filled_quantity: float = 0.0
        self.remaining_quantity: float = target_quantity
        self._order_counter: int = 0
        self._submitted_initial: bool = False

    def on_order_event(self, event: dict[str, Any]) -> None:
        """Process order lifecycle events from the execution engine."""
        self.events_log.append(event)
        etype = event.get("event_type")

        if etype == "ACCEPTED":
            self.active_order_id = event.get("order_id")
            self.active_order_qty = event.get("quantity", 0.0)
            self.active_order_price = event.get("price")
        elif etype in ("PARTIAL_FILL", "FILLED"):
            filled_chunk = event.get("fill_qty", 0.0)
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

    def on_fill(self, fill) -> None:
        """Hook called when a fill executes."""
        is_complete = (self.filled_quantity + fill.quantity) >= self.target_quantity
        evt = {
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

    def modify_order(
        self,
        bar: Bar,
        new_price: float,
        new_qty: float,
    ) -> None:
        """Modify an active order by cancelling remaining and submitting a new limit order."""
        old_id = self.active_order_id
        # 1. Log cancellation of remaining
        self.on_order_event(
            {
                "event_type": "CANCELLED",
                "order_id": old_id,
                "symbol": bar.instrument_id.symbol,
                "quantity": self.active_order_qty,
                "price": self.active_order_price,
                "remaining_qty": self.remaining_quantity,
                "message": "Order replaced for price modification",
            }
        )

        # 2. Submit replacement limit order
        self._order_counter += 1
        new_id = f"mod-order-{self._order_counter}"
        self.active_order_id = new_id
        self.active_order_price = new_price
        self.active_order_qty = new_qty

        intent = OrderIntent(
            bar.instrument_id,
            OrderSide.BUY,
            new_qty,
            OrderType.LIMIT,
            price=new_price,
        )
        self.submit(intent)

        self.on_order_event(
            {
                "event_type": "MODIFIED",
                "order_id": new_id,
                "symbol": bar.instrument_id.symbol,
                "quantity": new_qty,
                "price": new_price,
                "remaining_qty": self.remaining_quantity,
                "message": f"Modified order submitted at {new_price}",
            }
        )

    def on_bar(self, bar: Bar) -> None:
        # Initial order placement: submit buy limit order for target quantity
        if not self._submitted_initial:
            self._order_counter += 1
            order_id = f"order-{self._order_counter}"
            self.active_order_id = order_id
            limit_price = bar.close - 1.0  # limit slightly below market
            self.active_order_price = limit_price
            self.active_order_qty = self.target_quantity

            # Emit initial submission event
            self.on_order_event(
                {
                    "event_type": "SUBMITTED",
                    "order_id": order_id,
                    "symbol": bar.instrument_id.symbol,
                    "quantity": self.target_quantity,
                    "price": limit_price,
                    "remaining_qty": self.remaining_quantity,
                }
            )
            # Accept event
            self.on_order_event(
                {
                    "event_type": "ACCEPTED",
                    "order_id": order_id,
                    "symbol": bar.instrument_id.symbol,
                    "quantity": self.target_quantity,
                    "price": limit_price,
                    "remaining_qty": self.remaining_quantity,
                }
            )

            # Submit through context
            self.buy(bar.instrument_id, self.target_quantity)
            self._submitted_initial = True
            return

        # Check if active order needs modification (e.g. price moved away by modify_threshold)
        if (
            self.active_order_id is not None
            and self.active_order_price is not None
            and self.remaining_quantity > 0.0
            and bar.close - self.active_order_price >= self.modify_threshold
        ):
            # Price moved up away from limit; modify order closer to current close
            new_price = bar.close - 0.5
            self.modify_order(bar, new_price=new_price, new_qty=self.remaining_quantity)


def run(
    bars: int = 100,
    target_quantity: float = 20.0,
    modify_threshold: float = 2.0,
) -> dict[str, Any]:
    bars_list = trending_bars("RELIANCE", bars)
    strat = PartialFillStrategy(target_quantity=target_quantity, modify_threshold=modify_threshold)
    report = replay_report(
        strat,
        bars_list,
        {"target_quantity": target_quantity, "modify_threshold": modify_threshold},
        config_extra={"bars": bars},
    )
    # Augment result with order lifecycle event logs
    report["result"]["order_events"] = strat.events_log
    report["result"]["filled_quantity"] = strat.filled_quantity
    report["result"]["remaining_quantity"] = strat.remaining_quantity
    return report


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bars", type=int, default=100, help="number of synthetic bars")
    parser.add_argument(
        "--target-qty", type=float, default=20.0, dest="target_quantity", help="target quantity"
    )
    parser.add_argument(
        "--modify-threshold",
        type=float,
        default=2.0,
        help="price move threshold to trigger order modification",
    )


def main(argv: list[str] | None = None) -> int:
    return run_example(run, __doc__, _add_args, argv=argv)


if __name__ == "__main__":
    sys.exit(main())
