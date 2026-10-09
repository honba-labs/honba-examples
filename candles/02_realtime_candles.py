"""02_realtime_candles: build 1-minute OHLC frames from a stream of quote ticks.

Candles are not fetched, they are folded out of ticks. ``subscribe`` registers a callback
that runs inline on the transport — each push returns only after the frame is updated —
and every top-of-book ``QuoteTick`` opens a frame for its (instrument, UTC minute) pair if
there is none yet, otherwise extends the high/low and moves the close to the mid price.
Releasing the subscription and the connection in ``finally`` is what stops a run from
leaking either. The default adapter is ``fake``: deterministic and offline, it moves its
feed only when ``advance_prices`` asks — a ``FakeAdapter``-only test affordance, since a
real broker pushes on its own. Its clock is a fixed epoch advancing 1 ms per event, so a
run's ticks share one minute and the report shows a single frame; a real feed spread over
minutes yields one frame per minute.

The result is JSON-serialisable and deterministic: ``adapter``, ``advances`` (rounds of
price moves), ``timeframe`` (the frame interval), ``subscription`` (``id``, ``mode``,
``instruments``), ``ticks`` (one pushed quote per round, raw nanosecond ``ts``) and
``frames`` (one row per instrument-minute: ``date``, OHLC, ``tick_count``).

Run::

    python candles/02_realtime_candles.py
    python candles/02_realtime_candles.py --out frames.json
    python candles/02_realtime_candles.py --advances 5
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from honba.adapters.errors import AdapterNotFound
from honba.adapters.models import StreamMode
from honba.adapters.registry import default_registry
from honba.adapters.testing import FakeAdapter
from honba.domain.instrument import InstrumentId

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:  # plain checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba_examples.bars import bucket_start
from honba_examples.jsonable import jsonable

#: One candle series: the fake serves only RELIANCE and TCS, this example follows one.
_INSTRUMENT = InstrumentId("RELIANCE", "NSE")
#: Frame interval: ticks are bucketed exactly as ``1m`` bars are bucketed.
_TIMEFRAME = "1m"


def _registry():
    registry = default_registry()
    if "fake" not in registry.available():
        registry.register("fake", FakeAdapter)
    return registry


def _iso(ts: int) -> str:
    """ISO-8601 UTC instant of a nanosecond timestamp."""
    return dt.datetime.fromtimestamp(ts / 1e9, tz=dt.timezone.utc).isoformat()


def _tick_row(tick: Any) -> dict[str, Any]:
    iid = tick.instrument_id
    return {
        "symbol": iid.symbol,
        "exchange": iid.exchange,
        "ts": tick.ts,
        "bid_price": tick.bid_price,
        "ask_price": tick.ask_price,
        "bid_size": tick.bid_size,
        "ask_size": tick.ask_size,
        "mid_price": tick.mid_price,
    }


def _fold(ticks: list[Any], timeframe: str = _TIMEFRAME) -> list[dict[str, Any]]:
    """Fold ticks into OHLC frames bucketed per instrument per timeframe bucket.

    A tick opens a frame when its (instrument, bucket) pair has none yet; otherwise it
    extends the high/low and moves the close to its mid price. The frame's ``date`` is the
    first nanosecond of the bucket, the stamp a bar of that timeframe would carry.
    """
    frames: list[dict[str, Any]] = []
    current: tuple[InstrumentId, int] | None = None
    for tick in ticks:
        price = tick.mid_price
        key = (tick.instrument_id, bucket_start(tick.ts, timeframe))
        if key != current:
            current = key
            frames.append(
                {
                    "date": _iso(key[1]),
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "tick_count": 1,
                }
            )
            continue
        frame = frames[-1]
        frame["high"] = max(frame["high"], price)
        frame["low"] = min(frame["low"], price)
        frame["close"] = price
        frame["tick_count"] += 1
    return frames


def _advance(adapter: Any, advances: int) -> None:
    """Move the fake's feed ``advances`` times.

    ``advance_prices`` is a ``FakeAdapter`` test affordance, not part of the adapter
    contract: real brokers push quotes to the callback on their own, so there is nothing
    to advance there.
    """
    if not isinstance(adapter, FakeAdapter):
        return
    for _ in range(advances):
        adapter.advance_prices(_INSTRUMENT)


async def _stream(adapter: Any, advances: int = 3) -> dict[str, Any]:
    """Connect, subscribe, let the feed move, then release everything on the way out.

    The callback appends inline, so a tick is already collected when ``advance_prices``
    returns; both handles are dropped in a ``finally``, so a fold that raises mid-stream
    still unsubscribes, and a failed unsubscribe still disconnects.
    """
    await adapter.connect()
    try:
        ticks: list[Any] = []
        subscription = await adapter.subscribe(
            instruments=(_INSTRUMENT,), mode=StreamMode.QUOTE, callback=ticks.append
        )
        try:
            _advance(adapter, advances)
        finally:
            await adapter.unsubscribe(subscription.id)
        return {
            "adapter": adapter.name,
            "advances": advances,
            "timeframe": _TIMEFRAME,
            "subscription": jsonable(subscription),
            "ticks": [_tick_row(tick) for tick in ticks],
            "frames": _fold(ticks),
        }
    finally:
        await adapter.disconnect()


def run(
    adapter: str = "fake", config: dict[str, str] | None = None, advances: int = 3
) -> dict[str, Any]:
    """Stream quotes, fold them into frames, and return a JSON-serializable, deterministic
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
    parser.add_argument("--advances", type=int, default=3, help="price advances (fake only)")
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
