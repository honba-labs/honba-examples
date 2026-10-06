"""05_check_positions: read the account books - positions, funds, trades - after a round trip.

``positions()`` reports what you hold per instrument (``side``, ``quantity``, ``avg_price``
and a ``realized_pnl`` booked in integer minor units), ``funds()`` the cash, and ``trades()`` the
day's fills. The example buys 10 RELIANCE and sells 4 of them, so the position shrinks to 6
and books the bid-ask spread as realized P&L, then buys and sells 5 TCS to close it: a flat
position is omitted from ``positions()``. I/O is gated by capabilities, so
``Capability.HOLDINGS`` is checked against ``adapter.capabilities().features`` first and
reported as unsupported when the adapter does not declare it, instead of letting the call
fail. The default adapter is ``fake``: deterministic and offline, so the example runs
anywhere. Point it at a real broker with
``--adapter dhan --config client_id=... --config access_token=...``.

The result is JSON-serialisable and deterministic: ``adapter``, ``position_after_buy`` (the
position the buy opened), ``positions`` (the non-flat book after the round trips, each row
carrying ``realized_pnl`` in minor units and ``realized_pnl_rupees`` for display),
``flat_omitted`` (a TCS round trip that closed, plus the proof it is no longer reported),
``holdings`` (the capability check as data), ``funds`` and ``trades``.

Run::

    python basic/05_check_positions.py
    python basic/05_check_positions.py --out positions.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from honba.adapters.capabilities import Capability
from honba.adapters.errors import AdapterNotFound
from honba.adapters.models import Product
from honba.adapters.registry import default_registry
from honba.adapters.testing import FakeAdapter
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent
from honba.domain.position import Position

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


def _position_row(position: Position) -> dict[str, Any]:
    """One position as the report shows it: ``realized_pnl`` stays integer minor units (ADR 0011)
    and gains the rupee figure a reader wants at a glance."""
    row = jsonable(position)
    row["realized_pnl_rupees"] = position.realized_pnl.to_major()
    return row


async def _holdings(adapter: Any) -> dict[str, Any]:
    """Demat holdings, or the fact that this adapter will not serve them.

    ``capabilities()`` is pure data and free of I/O, so the check happens before any call;
    asking for the capability anyway raises ``CapabilityError`` from the same descriptor.
    """
    if Capability.HOLDINGS not in adapter.capabilities().features:
        return {
            "supported": False,
            "capability": Capability.HOLDINGS.value,
            "reason": f"adapter {adapter.name} does not declare {Capability.HOLDINGS.value}",
        }
    return {"supported": True, "holdings": jsonable(await adapter.holdings())}


async def _check(adapter_name: str, config: dict[str, str]) -> dict[str, Any]:
    adapter = _registry().create(adapter_name, **config)
    await adapter.connect()
    try:
        buy_intent = OrderIntent.market_buy(_RELIANCE, 10)
        await adapter.place_order(
            buy_intent, product=Product.DELIVERY, client_order_id="reliance-buy"
        )
        opened = await adapter.positions()
        position_after_buy = _position_row(opened[0]) if opened else None

        await adapter.place_order(
            OrderIntent.market_sell(_RELIANCE, 4),
            product=Product.DELIVERY,
            client_order_id="reliance-sell",
        )
        tcs_buy = await adapter.place_order(
            OrderIntent.market_buy(_TCS, 5),
            product=Product.DELIVERY,
            client_order_id="tcs-buy",
        )
        tcs_sell = await adapter.place_order(
            OrderIntent.market_sell(_TCS, 5),
            product=Product.DELIVERY,
            client_order_id="tcs-sell",
        )

        positions = await adapter.positions()
        return {
            "adapter": adapter.name,
            "position_after_buy": position_after_buy,
            "positions": [_position_row(position) for position in positions],
            "flat_omitted": {
                "instrument": jsonable(_TCS),
                "bought": tcs_buy.filled_quantity,
                "sold": tcs_sell.filled_quantity,
                "in_positions": any(p.instrument_id == _TCS for p in positions),
            },
            "holdings": await _holdings(adapter),
            "funds": jsonable(await adapter.funds()),
            "trades": jsonable(await adapter.trades()),
        }
    finally:
        await adapter.disconnect()


def run(adapter: str = "fake", config: dict[str, str] | None = None) -> dict[str, Any]:
    """Buy, sell part of it, and read the account books back: a JSON-serializable,
    deterministic result (shape documented in the module docstring)."""
    return asyncio.run(_check(adapter, config or {}))


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
