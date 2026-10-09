"""03_custom_aggregation: fold 1m bars into a coarser timeframe with the OHLC rule.

Adapters serve the timeframes they serve; the ``15m``, ``1h`` or ``1w`` bars a research
workflow needs are derived from the finest bars it trusts. This example fetches one source
timeframe once, folds it into the timeframe given on the command line, and reports the two
side by side: the rule in words, the input and output counts, and one row per aggregated
bar with the number of source bars behind it.

The rule is fixed: inside a bucket the open comes from the first bar, the high and low are
the extremes of the members, the close from the last bar, and volume is summed, while the
aggregated bar keeps the first member's timestamp. A bucket is the right-open UTC interval
``[start, start + timeframe)``, so a bar stamped 09:19 closes the 09:15 bucket and a bar
stamped 09:20 opens the next one. The coarser timeframe is never sent to the adapter - only
the source timeframe is - and an unknown vocabulary (``2h``) raises ``ValueError`` from
``timeframe_seconds`` before any I/O happens.

The default adapter is ``fake``: deterministic, offline, and a test double rather than an
aggregator. Whatever timeframe it is asked for, it answers with the same one-minute series,
which is exactly why this example always fetches ``1m`` and resamples itself.

Run::

    python candles/03_custom_aggregation.py --timeframe 5m
    python candles/03_custom_aggregation.py --timeframe 15m --out aggregation.json
    python candles/03_custom_aggregation.py --timeframe 1w
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
from honba.adapters.registry import default_registry
from honba.adapters.testing import FakeAdapter
from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:  # plain checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba_examples.bars import SUPPORTED_TIMEFRAMES, aggregate, bucket_start, timeframe_seconds
from honba_examples.jsonable import jsonable

_INSTRUMENT = InstrumentId("RELIANCE", "NSE")
_SOURCE_TIMEFRAME = "1m"
_START = dt.datetime(2025, 6, 2, 9, 15, tzinfo=dt.timezone.utc)
_END = _START + dt.timedelta(days=1)
#: The fake serves five bars per request by default - one 5m bucket and nothing after it -
#: so the example asks it for a page wide enough to fold into real buckets.
_BARS_PER_REQUEST = 120
_RULE = (
    "open from the first bar in the bucket, high/low as the extremes, close from the last "
    "bar, volume summed; buckets are right-open UTC intervals [start, start + timeframe) "
    "and the aggregated bar keeps the first bar's timestamp"
)


def _registry():
    registry = default_registry()
    if "fake" not in registry.available():
        registry.register("fake", FakeAdapter)
    return registry


def _build(adapter_name: str, config: dict[str, str]) -> Any:
    """Create the adapter, giving the fake enough 1m bars to fold into several buckets.

    ``bars_per_request`` reaches a real adapter only if the user passes it: it is a test
    double affordance, not part of the adapter contract. A value arriving as text from
    ``--config`` is converted on the way in.
    """
    settings: dict[str, Any] = dict(config)
    if adapter_name == "fake":
        settings.setdefault("bars_per_request", _BARS_PER_REQUEST)
        if isinstance(settings.get("bars_per_request"), str):
            settings["bars_per_request"] = int(settings["bars_per_request"])
    return _registry().create(adapter_name, **settings)


async def _fetch_bars(adapter_name: str, config: dict[str, str]) -> list[Bar]:
    """Fetch the source timeframe once; the adapter only ever sees ``1m``, never the target."""
    adapter = _build(adapter_name, config)
    await adapter.connect()
    try:
        return await adapter.historical_bars(
            _INSTRUMENT,
            timeframe=_SOURCE_TIMEFRAME,
            start=_START,
            end=_END,
        )
    finally:
        await adapter.disconnect()


def _date(ts: int) -> str:
    """ISO-8601 UTC stamp of a bar's ``ts`` (unix nanoseconds)."""
    return dt.datetime.fromtimestamp(ts / 1e9, tz=dt.timezone.utc).isoformat()


def _row(bar: Bar, constituents: int) -> dict[str, Any]:
    return {
        "date": _date(bar.ts),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "constituents": constituents,
    }


def _constituent_counts(bars: list[Bar], timeframe: str) -> dict[int, int]:
    """How many source bars fell into each bucket, keyed by bucket start."""
    counts: dict[int, int] = {}
    for bar in bars:
        key = bucket_start(bar.ts, timeframe)
        counts[key] = counts.get(key, 0) + 1
    return counts


def run(
    timeframe: str = "5m", *, adapter: str = "fake", config: dict[str, str] | None = None
) -> dict[str, Any]:
    """Fetch ``1m`` bars and fold them into ``timeframe``; shape in the module docstring.

    ``timeframe_seconds`` runs first, so an unknown vocabulary fails before the adapter is
    even created, let alone asked for data.
    """
    timeframe_seconds(timeframe)
    source = asyncio.run(_fetch_bars(adapter, config or {}))
    counts = _constituent_counts(source, timeframe)
    aggregated = aggregate(source, timeframe)
    return jsonable(
        {
            "adapter": adapter,
            "instrument": _INSTRUMENT,
            "source_timeframe": _SOURCE_TIMEFRAME,
            "target_timeframe": timeframe,
            "rule": _RULE,
            "input_count": len(source),
            "output_count": len(aggregated),
            "bars": [_row(bar, counts[bucket_start(bar.ts, timeframe)]) for bar in aggregated],
        }
    )


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
    parser.add_argument(
        "--timeframe",
        default="5m",
        help="target bucket to derive: " + ", ".join(SUPPORTED_TIMEFRAMES),
    )
    parser.add_argument("--adapter", default="fake", help="registered adapter name")
    parser.add_argument("--config", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON here")
    args = parser.parse_args(argv)

    try:
        result = run(args.timeframe, adapter=args.adapter, config=_parse_config(args.config))
    except (ValueError, AdapterNotFound) as exc:
        raise SystemExit(str(exc))

    text = json.dumps(result, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
