"""03_subscribe_quotes: stream live quotes and compare them with one-shot snapshots.

``subscribe`` registers a callback and hands back a ``Subscription`` handle: every push
arrives as a ``StreamEvent`` (here a top-of-book ``QuoteTick``), and that handle is what
you later pass to ``unsubscribe`` — releasing it in a ``finally`` is what stops a run from
leaking subscriptions. ``quote`` is the one-shot counterpart: a single top-of-book
snapshot. Asking for a capability the adapter does not declare raises ``CapabilityError``
before any I/O, so an unsupported request costs nothing. The default adapter is ``fake``:
deterministic and offline, so the example runs anywhere; it only moves its feed when
``advance_prices`` asks, while a real broker pushes on its own.

The result is JSON-serialisable and deterministic: ``adapter``, ``advances`` (rounds of
price moves), ``subscription`` (``id``, ``mode``, ``instruments``), ``ticks`` (one pushed
quote per instrument per round), ``snapshot`` (a one-shot ``quote`` of RELIANCE taken
where the stream stopped) and ``unsupported`` (capabilities refused as data, keyed by
capability, valued by the refusal message).

Run::

    python basic/03_subscribe_quotes.py
    python basic/03_subscribe_quotes.py --out quotes.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from honba.adapters.capabilities import Capability
from honba.adapters.errors import AdapterNotFound, CapabilityError
from honba.adapters.models import StreamMode
from honba.adapters.registry import default_registry
from honba.adapters.testing import FakeAdapter
from honba.domain.instrument import InstrumentId

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:  # plain checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba_examples.jsonable import jsonable

#: The two instruments the fake adapter serves; a real run picks its own.
_INSTRUMENTS = (InstrumentId("RELIANCE", "NSE"), InstrumentId("TCS", "NSE"))


def _registry():
    registry = default_registry()
    if "fake" not in registry.available():
        registry.register("fake", FakeAdapter)
    return registry


def _quote_row(quote: Any) -> dict[str, Any]:
    iid = quote.instrument_id
    return {
        "symbol": iid.symbol,
        "exchange": iid.exchange,
        "ts": quote.ts,
        "bid_price": quote.bid_price,
        "ask_price": quote.ask_price,
        "bid_size": quote.bid_size,
        "ask_size": quote.ask_size,
        "mid_price": quote.mid_price,
    }


def _refusals(adapter: Any) -> dict[str, str]:
    """Capabilities this adapter will not serve, reported as data instead of a traceback.

    ``capabilities()`` is pure data, so asking it first refuses before any I/O; calling
    ``adapter.depth()`` anyway raises the same ``CapabilityError`` from the same
    descriptor, never from a failed request.
    """
    try:
        adapter.capabilities().require(Capability.DEPTH)
    except CapabilityError as exc:
        return {"depth": str(exc)}
    return {}


def _advance(adapter: Any, advances: int) -> None:
    """Move the fake's feed ``advances`` times per subscribed instrument.

    ``advance_prices`` is a ``FakeAdapter`` test affordance, not part of the adapter
    contract: real brokers push quotes to the callback on their own, so there is nothing
    to advance there.
    """
    if not isinstance(adapter, FakeAdapter):
        return
    for _ in range(advances):
        for instrument_id in _INSTRUMENTS:
            adapter.advance_prices(instrument_id)


async def _stream(adapter: Any, advances: int = 2) -> dict[str, Any]:
    """Connect, subscribe, let the feed move, then release everything on the way out.

    Both handles are dropped in a ``finally``: a callback that raises mid-stream still
    unsubscribes, and a failed unsubscribe still disconnects.
    """
    await adapter.connect()
    try:
        ticks: list[Any] = []
        subscription = await adapter.subscribe(
            instruments=_INSTRUMENTS, mode=StreamMode.QUOTE, callback=ticks.append
        )
        try:
            _advance(adapter, advances)
        finally:
            await adapter.unsubscribe(subscription.id)
        snapshot = await adapter.quote(_INSTRUMENTS[0])
        return {
            "adapter": adapter.name,
            "advances": advances,
            "subscription": jsonable(subscription),
            "ticks": [_quote_row(tick) for tick in ticks],
            "snapshot": _quote_row(snapshot),
            "unsupported": _refusals(adapter),
        }
    finally:
        await adapter.disconnect()


def run(
    adapter: str = "fake", config: dict[str, str] | None = None, advances: int = 2
) -> dict[str, Any]:
    """Stream quotes and take a snapshot, returning a JSON-serializable, deterministic
    result (shape documented in the module docstring)."""
    return asyncio.run(_stream(_registry().create(adapter, **(config or {})), advances))


def _parse_config(pairs: list[str]) -> dict[str, str]:
    config: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise SystemExit(f"--config expects KEY=VALUE, got {pair!r}")
        config[key] = value
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--adapter", default="fake", help="registered adapter name")
    parser.add_argument("--config", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument(
        "--advances", type=int, default=2, help="price advances per instrument (fake only)"
    )
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON here")
    args = parser.parse_args(argv)

    try:
        result = run(args.adapter, _parse_config(args.config), args.advances)
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
