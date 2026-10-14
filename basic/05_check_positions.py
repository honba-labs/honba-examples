"""05_check_positions: read the account books - positions, funds, trades - after a round trip.

``positions()`` reports what you hold per instrument (``side``, ``quantity``, ``avg_price``
and a ``realized_pnl`` booked in integer minor units), ``funds()`` the cash, and ``trades()`` the
day's fills. The example buys 10 RELIANCE and sells 4 of them, so the position shrinks to 6
and books the bid-ask spread as realized P&L, then buys and sells 5 TCS to close it: a flat
position is omitted from ``positions()``. I/O is gated by capabilities, so
``Capability.HOLDINGS`` is checked against ``adapter.capabilities().features`` first and
reported as unsupported when the adapter does not declare it, instead of letting the call
fail. The default adapter is ``fake``: deterministic and offline, so the example runs
anywhere. The round trip places real orders on any other adapter, so a real broker
(``--adapter dhan --config client_id=... --config access_token=...``) is refused unless
``HONBA_EXAMPLES_ALLOW_LIVE_ORDERS=1`` is set.

The result is JSON-serialisable and deterministic: ``adapter``, ``position_after_buy`` (the
position the buy opened), ``positions`` (the non-flat book after the round trips, each row
carrying ``realized_pnl`` in minor units and ``realized_pnl_rupees`` for display),
``flat_omitted`` (a TCS round trip that closed, plus the proof it is no longer reported),
``holdings`` (the capability check as data), ``funds`` and ``trades``.

Run::

    python basic/05_check_positions.py
    python basic/05_check_positions.py --out positions.json
    python basic/05_check_positions.py --format json
"""

from __future__ import annotations

import asyncio
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba_examples.cli import add_out_arg, emit_json, make_parser, require_paper_adapter
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


def _position_of(positions: list[Position], instrument_id: InstrumentId) -> Position | None:
    """The position in ``instrument_id``; a real account may already hold other instruments."""
    return next((p for p in positions if p.instrument_id == instrument_id), None)


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
    adapter = _registry().create_full(adapter_name, **config)
    await adapter.connect()
    try:
        buy_intent = OrderIntent.market_buy(_RELIANCE, 10)
        await adapter.place_order(
            buy_intent, product=Product.DELIVERY, client_order_id="reliance-buy"
        )
        opened = await adapter.positions()
        bought = _position_of(opened, _RELIANCE)
        position_after_buy = _position_row(bought) if bought else None

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
    require_paper_adapter(adapter)
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
    parser = make_parser(__doc__)
    parser.add_argument("--adapter", default="fake", help="registered adapter name")
    parser.add_argument("--config", action="append", default=[], metavar="KEY=VALUE")
    add_out_arg(parser)
    parser.add_argument(
        "--format",
        choices=["auto", "table", "json"],
        default="auto",
        help="output format (default auto: rich table in interactive terminal, json for pipes/exchange)",
    )
    args = parser.parse_args(argv)

    try:
        result = run(args.adapter, _parse_config(args.config))
    except (AdapterNotFound, ValueError) as exc:
        raise SystemExit(str(exc))

    is_table = (args.format == "table") or (
        args.format == "auto" and sys.stdout.isatty() and not args.out
    )

    if is_table:
        from honba.display import Column, render_kv, render_table

        funds = result.get("funds", {})
        render_kv(
            [
                ("Adapter", result.get("adapter")),
                ("Available Cash", f"₹{funds.get('available_cash', 0):,.2f}"),
                ("Opening Balance", f"₹{funds.get('opening_balance', 0):,.2f}"),
                ("Currency", funds.get("currency", "INR")),
            ],
            title="Account Funds",
        )

        pos_rows = [
            {
                "symbol": p["instrument_id"]["symbol"],
                "exchange": p["instrument_id"]["exchange"],
                "side": p["side"].upper(),
                "quantity": p["quantity"],
                "avg_price": f"₹{p['avg_price']:,.2f}",
                "realized_pnl": f"₹{p.get('realized_pnl_rupees', 0.0):+,.2f}",
            }
            for p in result.get("positions", [])
        ]
        if pos_rows:
            render_table(
                pos_rows,
                [
                    Column("symbol", "Symbol", style="cyan"),
                    Column("exchange", "Exchange", style="magenta"),
                    Column("side", "Side", style="bold yellow"),
                    Column("quantity", "Quantity", align="right"),
                    Column("avg_price", "Avg Price", align="right", style="green"),
                    Column("realized_pnl", "Realized P&L", align="right", style="magenta"),
                ],
                title="Open Positions",
            )

        trade_rows = [
            {
                "symbol": t["instrument_id"]["symbol"],
                "exchange": t["instrument_id"]["exchange"],
                "side": t["side"].upper(),
                "quantity": t["quantity"],
                "price": f"₹{t['price']:,.2f}",
            }
            for t in result.get("trades", [])
        ]
        if trade_rows:
            render_table(
                trade_rows,
                [
                    Column("symbol", "Symbol", style="cyan"),
                    Column("exchange", "Exchange", style="magenta"),
                    Column("side", "Side", style="bold yellow"),
                    Column("quantity", "Quantity", align="right"),
                    Column("price", "Price", align="right", style="green"),
                ],
                title="Executed Trades",
            )
    else:
        emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
