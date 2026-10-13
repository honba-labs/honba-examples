"""backtesting/02_cost_modeling: India equity delivery costs in the backtest.

Demonstrates the per-leg cost breakdown (brokerage, STT, exchange, SEBI, IPFT,
stamp duty, GST) that Honba's cost schedule applies to every fill. The same
cost function is used in live trading through the adapter layer.

Run::

    python backtesting/02_cost_modeling.py
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
