"""backtesting/02_cost_modeling: India equity delivery costs in the backtest.

Demonstrates the per-leg cost breakdown (brokerage, STT, exchange, SEBI, IPFT,
stamp duty, GST) that Honba's cost schedule applies to every fill. The same
cost function is used in live trading through the adapter layer.

Run::

    python backtesting/02_cost_modeling.py

Pitfalls::

    - Schedule, not history: the cost functions take (side, quantity, price) and no date, so the
      numbers are the rates bundled with the installed Honba, not those in force on a past trade
      date. Charges change; a multi-year backtest needs the rate that applied at each fill.
    - Delivery only: these are the equity delivery legs. Intraday, F&O and ETF trades have different
      STT and stamp rules, so reusing them for a faster strategy misstates cost.
    - Check the STT leg: in the default output STT is 0.0 on the buy and charged only on the sell.
      Compare that against the current NSE/SEBI circular before relying on it, because a missing leg
      under-counts every round trip.
    - Both legs, every round trip: the buy and sell totals are printed separately because a position
      pays both, and the small-order floor (brokerage, stamp) weighs more on small tickets. A
      strategy that trades often needs a larger gross edge, so run costs inside the backtest, not
      afterwards.
    - Statutory charges are only part of friction: spread, slippage, market impact and whether the
      quantity is even available at the price are not modelled here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba.domain.order import OrderSide
from honba.markets.india.costs import (
    nse_equity_delivery_breakdown,
    nse_equity_delivery_fill_cost,
)

from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.jsonable import jsonable

__all__ = ["main", "run"]


def run(
    price: float = 2500.0,
    qty: float = 10.0,
) -> dict[str, Any]:
    buy_legs = nse_equity_delivery_breakdown(OrderSide.BUY, qty, price)
    sell_legs = nse_equity_delivery_breakdown(OrderSide.SELL, qty, price)

    cost_buy = nse_equity_delivery_fill_cost(OrderSide.BUY, qty, price)
    cost_sell = nse_equity_delivery_fill_cost(OrderSide.SELL, qty, price)

    return {
        "price": price,
        "quantity": qty,
        "notional": qty * price,
        "buy": {
            "legs": jsonable(buy_legs),
            "total_rupees": cost_buy.to_major(),
            "total_minor": cost_buy.amount,
        },
        "sell": {
            "legs": jsonable(sell_legs),
            "total_rupees": cost_sell.to_major(),
            "total_minor": cost_sell.amount,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--price", type=float, default=2500.0)
    parser.add_argument("--qty", type=float, default=10.0)
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(price=args.price, qty=args.qty)

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
