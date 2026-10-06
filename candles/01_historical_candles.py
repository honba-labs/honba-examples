"""01_historical_candles: pull a window of bars with ``adapter.historical_bars``.

``historical_bars`` takes a canonical timeframe name (``"1m"``, ``"5m"``, ``"1d"`` — exact,
case-sensitive strings, never broker shorthand), a half-open window ``[start, end)`` whose
bounds must both be tz-aware datetimes, and returns bars in ascending time order. Each bar
reports its nanosecond ``ts`` as an ISO-8601 date, so the report is one row per bar plus a
summary of the window. The default adapter is ``fake``: a test double, **not** an
aggregator — it answers every timeframe with the same five one-minute bars of the
2025-06-02 09:15–09:19 UTC session (``bars_per_request``, spaced one minute apart, filtered
to the window) instead of resampling anything, so nothing here should be mistaken for a
real feed.

Run::

    python candles/01_historical_candles.py
    python candles/01_historical_candles.py --out bars.json
    python candles/01_historical_candles.py --timeframe 1m --instrument TCS
    python candles/01_historical_candles.py --start 2025-06-02T09:16:00+00:00 --end 2025-06-02T09:18:00+00:00
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from honba.adapters.errors import AdapterError
from honba.adapters.registry import default_registry
from honba.adapters.testing import FakeAdapter
from honba.domain.instrument import InstrumentId

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:  # plain checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba_examples.jsonable import jsonable

#: The fake's whole history: five bars, one minute apart from this instant, UTC.
_SESSION_START = dt.datetime(2025, 6, 2, 9, 15, tzinfo=dt.timezone.utc)
_DEFAULT_WINDOW = (_SESSION_START, _SESSION_START + dt.timedelta(minutes=5))
_EXCHANGE = "NSE"


def _registry():
    registry = default_registry()
    if "fake" not in registry.available():
        registry.register("fake", FakeAdapter)
    return registry


def _iso(ts: int) -> str:
    """ISO-8601 UTC instant of a nanosecond timestamp."""
    return dt.datetime.fromtimestamp(ts / 1e9, tz=dt.timezone.utc).isoformat()


def _bar_row(bar: Any) -> dict[str, Any]:
    return {
        "date": _iso(bar.ts),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }


def _require_tz_aware(name: str, value: dt.datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a tz-aware datetime, got {value.isoformat()}")


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Window summary; an empty window reports nulls rather than raising."""
    if not rows:
        return {"first_date": None, "last_date": None, "min_low": None, "max_high": None}
    return {
        "first_date": rows[0]["date"],
        "last_date": rows[-1]["date"],
        "min_low": min(row["low"] for row in rows),
        "max_high": max(row["high"] for row in rows),
    }


async def _fetch(
    adapter: Any,
    *,
    instrument_id: InstrumentId,
    timeframe: str,
    start: dt.datetime,
    end: dt.datetime,
) -> dict[str, Any]:
    await adapter.connect()
    try:
        bars = await adapter.historical_bars(
            instrument_id, timeframe=timeframe, start=start, end=end
        )
        rows = [_bar_row(bar) for bar in bars]
    finally:
        await adapter.disconnect()
    return {
        "adapter": adapter.name,
        "instrument": jsonable(instrument_id),
        "timeframe": timeframe,
        "window": {"start": jsonable(start), "end": jsonable(end)},
        "count": len(rows),
        "bars": rows,
        "summary": _summary(rows),
    }


def run(
    adapter: str = "fake",
    config: dict[str, str] | None = None,
    *,
    instrument: str = "RELIANCE",
    timeframe: str = "1m",
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
) -> dict[str, Any]:
    """Fetch ``[start, end)`` bars and return a JSON-serializable, deterministic result
    (shape documented in the module docstring)."""
    start = _DEFAULT_WINDOW[0] if start is None else start
    end = _DEFAULT_WINDOW[1] if end is None else end
    _require_tz_aware("start", start)
    _require_tz_aware("end", end)
    instrument_id = InstrumentId(instrument, _EXCHANGE)
    return asyncio.run(
        _fetch(
            _registry().create(adapter, **(config or {})),
            instrument_id=instrument_id,
            timeframe=timeframe,
            start=start,
            end=end,
        )
    )


def _parse_config(pairs: list[str]) -> dict[str, str]:
    config: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise SystemExit(f"--config expects KEY=VALUE, got {pair!r}")
        config[key] = value
    return config


def _parse_bound(flag: str, text: str | None) -> dt.datetime | None:
    if text is None:
        return None
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        raise SystemExit(
            f"{flag} expects an ISO-8601 datetime with an offset, "
            f"like 2025-06-02T09:15:00+00:00, got {text!r}"
        ) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--adapter", default="fake", help="registered adapter name")
    parser.add_argument("--config", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--instrument", default="RELIANCE", help="symbol to fetch")
    parser.add_argument("--timeframe", default="1m", help="canonical timeframe: 1m, 5m or 1d")
    parser.add_argument(
        "--start", default=None, help="tz-aware ISO start of the half-open [start, end) window"
    )
    parser.add_argument("--end", default=None, help="tz-aware ISO end of the window (exclusive)")
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON here")
    args = parser.parse_args(argv)

    try:
        result = run(
            args.adapter,
            _parse_config(args.config),
            instrument=args.instrument,
            timeframe=args.timeframe,
            start=_parse_bound("--start", args.start),
            end=_parse_bound("--end", args.end),
        )
    except (AdapterError, ValueError) as exc:
        raise SystemExit(str(exc))

    text = json.dumps(result, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
