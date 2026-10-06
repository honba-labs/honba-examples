"""04_multi_timeframe: one 1m fetch, several views, and the invariants that tie them together.

A research workflow should read one source of truth and resample it; fetching the same
window at several resolutions and hoping they agree is how silent data bugs get into a
backtest. This example pulls ``1m`` bars once, derives ``5m``, ``1h`` and ``1d`` with
``honba_examples.bars.aggregate``, and reports every view with its bar count, volume and
span, plus an ``alignment`` block that runs the checks a workflow should run before it
trusts a resampled series.

For each pair (``1m->5m``, ``5m->1h``, ``1h->1d``) the block reports whether every fine
bar falls in exactly one coarse bucket (``covered``), whether each coarse bar is the OHLCV
fold of its own constituents (``fold_match``, recomputed from the fine bars alone so an
aggregator bug cannot pass silently), whether the two levels span the same first and last
bucket (``span_match``), and whether bar counts and volume totals reconcile
(``constituents_total`` against ``fine_count``, ``volume_fine`` against ``volume_coarse``);
``aligned`` is their conjunction.

The default adapter is ``fake``: deterministic, offline, and a test double rather than an
aggregator - it answers any supported request with the same one-minute series. Its history
never leaves one UTC day, so the ``1d`` view holds a single bar here; the day-boundary case
is proved against synthetic multi-day bars in the tests instead.

Run::

    python candles/04_multi_timeframe.py
    python candles/04_multi_timeframe.py --out multi_timeframe.json
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

from honba_examples.bars import aggregate, bucket_start
from honba_examples.jsonable import jsonable

_INSTRUMENT = InstrumentId("RELIANCE", "NSE")
_SOURCE_TIMEFRAME = "1m"
_VIEWS = ("1m", "5m", "1h", "1d")
_PAIRS = (("1m", "5m"), ("5m", "1h"), ("1h", "1d"))
_START = dt.datetime(2025, 6, 2, 9, 15, tzinfo=dt.timezone.utc)
_END = _START + dt.timedelta(days=1)
#: The fake serves five bars per request by default - far too few for four resolutions.
_BARS_PER_REQUEST = 120


def _registry():
    registry = default_registry()
    if "fake" not in registry.available():
        registry.register("fake", FakeAdapter)
    return registry


def _build(adapter_name: str, config: dict[str, str]) -> Any:
    """Create the adapter, giving the fake enough 1m bars to fold into real buckets.

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
    """Fetch the one source series every view below is derived from."""
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


def _row(bar: Bar) -> dict[str, Any]:
    return {
        "date": _date(bar.ts),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }


def _section(bars: list[Bar]) -> dict[str, Any]:
    """One view: how many bars, how much volume, and the span they cover."""
    return {
        "bar_count": len(bars),
        "volume": sum(bar.volume for bar in bars),
        "first_date": _date(bars[0].ts) if bars else None,
        "last_date": _date(bars[-1].ts) if bars else None,
        "bars": [_row(bar) for bar in bars],
    }


def _alignment(
    fine: list[Bar], fine_timeframe: str, coarse: list[Bar], coarse_timeframe: str
) -> dict[str, Any]:
    """The invariants a resampled series must satisfy, checked without ``aggregate``.

    The fold is recomputed from the fine bars alone, so a bug in the aggregator shows up
    as ``fold_match: false`` instead of passing on its own word. This example watches one
    instrument; a multi-instrument workflow runs the same check once per instrument.
    """
    fine_buckets: dict[int, list[Bar]] = {}
    for bar in fine:
        fine_buckets.setdefault(bucket_start(bar.ts, coarse_timeframe), []).append(bar)
    coarse_keys = [bucket_start(bar.ts, coarse_timeframe) for bar in coarse]

    covered = len(coarse_keys) == len(set(coarse_keys)) and set(coarse_keys) == set(fine_buckets)
    fold_match = True
    for key, host in zip(coarse_keys, coarse):
        members = sorted(fine_buckets.get(key, ()), key=lambda bar: bar.ts)
        if not members or (host.open, host.high, host.low, host.close) != (
            members[0].open,
            max(bar.high for bar in members),
            min(bar.low for bar in members),
            members[-1].close,
        ):
            fold_match = False
    if fine and coarse:
        span_match = _ends(fine, coarse_timeframe) == _ends(coarse, coarse_timeframe)
    else:
        span_match = not fine and not coarse

    volume_fine = sum(bar.volume for bar in fine)
    volume_coarse = sum(bar.volume for bar in coarse)
    constituents_total = sum(len(fine_buckets.get(key, ())) for key in coarse_keys)
    counts_match = constituents_total == len(fine) and len(coarse) == len(fine_buckets)
    volumes_match = volume_fine == volume_coarse
    return {
        "fine_timeframe": fine_timeframe,
        "coarse_timeframe": coarse_timeframe,
        "fine_count": len(fine),
        "coarse_count": len(coarse),
        "constituents_total": constituents_total,
        "volume_fine": volume_fine,
        "volume_coarse": volume_coarse,
        "covered": covered,
        "fold_match": fold_match,
        "span_match": span_match,
        "aligned": covered and fold_match and span_match and counts_match and volumes_match,
    }


def _ends(bars: list[Bar], timeframe: str) -> tuple[int, int]:
    """First and last bucket of ``bars`` at ``timeframe``."""
    stamps = [bar.ts for bar in bars]
    return bucket_start(min(stamps), timeframe), bucket_start(max(stamps), timeframe)


def run(*, adapter: str = "fake", config: dict[str, str] | None = None) -> dict[str, Any]:
    """Fetch ``1m`` once, derive every view from it, and check the views align.

    Shape (documented in the module docstring): the fetch metadata, one section per
    timeframe, and the ``alignment`` block.
    """
    source = asyncio.run(_fetch_bars(adapter, config or {}))
    views: dict[str, list[Bar]] = {_SOURCE_TIMEFRAME: source}
    for timeframe in _VIEWS[1:]:
        views[timeframe] = aggregate(source, timeframe)
    alignment = {
        f"{fine}->{coarse}": _alignment(views[fine], fine, views[coarse], coarse)
        for fine, coarse in _PAIRS
    }
    return jsonable(
        {
            "adapter": adapter,
            "instrument": _INSTRUMENT,
            "source_timeframe": _SOURCE_TIMEFRAME,
            "timeframes": list(views),
            **{timeframe: _section(bars) for timeframe, bars in views.items()},
            "alignment": alignment,
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
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--adapter", default="fake", help="registered adapter name")
    parser.add_argument("--config", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON here")
    args = parser.parse_args(argv)

    try:
        result = run(adapter=args.adapter, config=_parse_config(args.config))
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
