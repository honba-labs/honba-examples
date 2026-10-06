"""04_place_order_paper: turn an OrderIntent into an OrderReport and read the books back.

An ``OrderIntent`` is what the strategy wants; the ``OrderReport`` that comes back is what
the broker says happened. A market order fills at the touch, a limit that cannot cross the
touch rests as ``ACCEPTED`` until it is cancelled, and a broker refusal comes back as
``status=REJECTED`` with a ``reject_reason`` - data the run journals, never an exception.
``client_order_id`` makes placement idempotent, ``orders()`` and ``trades()`` are the day's
books, and ``funds()`` shows the cash the fill moved. The default adapter is ``fake``:
deterministic and offline, so the example runs anywhere; it echoes ``client_order_id`` as
``order_id`` while a real broker returns its own id. Point it at a real broker with
``--adapter dhan --config client_id=... --config access_token=...``.

The result is JSON-serialisable and deterministic: ``adapter``, ``product``,
``market_buy``/``resting_limit``/``rejected_buy`` (each an ``intent`` beside the ``report``
it produced), ``idempotent_repeat`` (the same placement twice, with the book counts taken
right after it, which prove nothing was booked twice), ``orders`` (the day's order book),
``trades`` (its fills) and ``funds`` (cash after the fill).

Run::

    python basic/04_place_order_paper.py
    python basic/04_place_order_paper.py --out paper_orders.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from honba.adapters.errors import AdapterNotFound
from honba.adapters.models import Product
from honba.adapters.registry import default_registry
from honba.adapters.testing import FakeAdapter
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:  # plain checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba_examples.jsonable import jsonable

#: The two instruments the fake adapter serves; a real run picks its own.
_RELIANCE = InstrumentId("RELIANCE", "NSE")
_TCS = InstrumentId("TCS", "NSE")


def _registry():
    registry = default_registry()
    if "fake" not in registry.available():
        registry.register("fake", FakeAdapter)
    return registry


async def _place_orders(adapter_name: str, config: dict[str, str]) -> dict[str, Any]:
    adapter = _registry().create(adapter_name, **config)
    await adapter.connect()
    try:
        market_intent = OrderIntent.market_buy(_RELIANCE, 10)
        first = await adapter.place_order(
            market_intent, product=Product.DELIVERY, client_order_id="reliance-buy"
        )
        repeat = await adapter.place_order(
            market_intent, product=Product.DELIVERY, client_order_id="reliance-buy"
        )
        idempotent = {
            "client_order_id": "reliance-buy",
            "same_report": jsonable(first) == jsonable(repeat),
            "order_count": len(await adapter.orders()),
            "trade_count": len(await adapter.trades()),
        }

        limit_intent = OrderIntent.limit_buy(_RELIANCE, 5, 2400.0)
        resting = await adapter.place_order(
            limit_intent, product=Product.DELIVERY, client_order_id="reliance-limit"
        )
        await adapter.cancel_order(resting.order_id)
        cancelled = await adapter.order_status(resting.order_id)

        oversized_intent = OrderIntent.market_buy(_TCS, 1000)
        rejected = await adapter.place_order(
            oversized_intent, product=Product.DELIVERY, client_order_id="tcs-oversized"
        )

        return {
            "adapter": adapter.name,
            "product": Product.DELIVERY.value,
            "market_buy": {"intent": jsonable(market_intent), "report": jsonable(first)},
            "idempotent_repeat": idempotent,
            "resting_limit": {
                "intent": jsonable(limit_intent),
                "accepted": jsonable(resting),
                "cancelled": jsonable(cancelled),
            },
            "rejected_buy": {"intent": jsonable(oversized_intent), "report": jsonable(rejected)},
            "orders": jsonable(await adapter.orders()),
            "trades": jsonable(await adapter.trades()),
            "funds": jsonable(await adapter.funds()),
        }
    finally:
        await adapter.disconnect()


def run(adapter: str = "fake", config: dict[str, str] | None = None) -> dict[str, Any]:
    """Place one order of each outcome, then read the books back: a JSON-serializable,
    deterministic result (shape documented in the module docstring)."""
    return asyncio.run(_place_orders(adapter, config or {}))


def _parse_config(pairs: list[str]) -> dict[str, str]:
    config: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise SystemExit(f"--config expects KEY=VALUE, got {pair!r}")
        config[key] = value
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--adapter", default="fake", help="registered adapter name")
    parser.add_argument("--config", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON here")
    args = parser.parse_args(argv)

    try:
        result = run(args.adapter, _parse_config(args.config))
    except AdapterNotFound as exc:
        raise SystemExit(str(exc))

    text = json.dumps(result, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
