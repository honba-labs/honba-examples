"""backtesting/02_cost_modeling: India equity delivery costs in the backtest.

Demonstrates the per-leg cost breakdown (brokerage, STT, exchange, SEBI, IPFT,
stamp duty, GST) that Honba's cost schedule applies to every fill. The same
cost function is used in live trading through the adapter layer.

Run::

    python backtesting/02_cost_modeling.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba.markets.india.costs import nse_equity_delivery_breakdown
from honba.domain.order import OrderSide
from honba_examples.jsonable import jsonable
from honba_examples.money import paise_to_rupees, delivery_cost_paise

__all__ = ["run", "main"]


def run(
    price: float = 2500.0,
    qty: float = 10.0,
) -> dict[str, Any]:
    buy_legs = nse_equity_delivery_breakdown(OrderSide.BUY, qty, price)
    sell_legs = nse_equity_delivery_breakdown(OrderSide.SELL, qty, price)

    return {
        "price": price,
        "quantity": qty,
        "notional": qty * price,
        "buy": {
            "legs": jsonable(buy_legs),
            "total_rupees": sum(
                paise_to_rupees(v) for v in [
                    buy_legs.brokerage, buy_legs.stt, buy_legs.exchange,
                    buy_legs.sebi, buy_legs.ipft, buy_legs.stamp_duty, buy_legs.gst
                ] if v is not None
            ),
            "total_paise": delivery_cost_paise(OrderSide.BUY, qty, price),
        },
        "sell": {
            "legs": jsonable(sell_legs),
            "total_rupees": sum(
                paise_to_rupees(v) for v in [
                    sell_legs.brokerage, sell_legs.stt, sell_legs.exchange,
                    sell_legs.sebi, sell_legs.ipft, sell_legs.stamp_duty, sell_legs.gst
                ] if v is not None
            ),
            "total_paise": delivery_cost_paise(OrderSide.SELL, qty, price),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--price", type=float, default=2500.0)
    parser.add_argument("--qty", type=float, default=10.0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(price=args.price, qty=args.qty)

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())