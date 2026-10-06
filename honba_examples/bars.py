"""honba_examples.bars: bucket bars into a higher timeframe (OHLC aggregation).

Honba has no resampler of its own, and none of the examples may reach for the
network to get higher-timeframe bars: an aggregation example has to show the
rule. This is that rule, pure and deterministic - the same inputs always give
the same bars, in the same order.

Bucketing is UTC (``Bar.ts`` is unix nanoseconds). ``ts`` of an aggregated bar is
the timestamp of the **first** bar that fell into its bucket, so a daily bar
built from NSE intraday bars lands on the 09:15 IST session open, which is the
stamp the Parquet store uses for daily bars.
"""

from __future__ import annotations

from collections.abc import Sequence

from honba.domain.bar import Bar

__all__ = ["SUPPORTED_TIMEFRAMES", "aggregate", "bucket_start", "timeframe_seconds"]

_NS_PER_SECOND = 1_000_000_000
_SECONDS = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "1d": 24 * 60 * 60,
}
_WEEK = 7 * 24 * 60 * 60

SUPPORTED_TIMEFRAMES: tuple[str, ...] = (*_SECONDS, "1w")
"""The vocabulary honba's query parser speaks (``1m`` ... ``1d``, plus ``1w``)."""


def timeframe_seconds(timeframe: str) -> int:
    """Bucket length in seconds, or ``ValueError`` naming the supported vocabulary."""
    try:
        return _WEEK if timeframe == "1w" else _SECONDS[timeframe]
    except KeyError:
        raise ValueError(
            f"unknown timeframe {timeframe!r}; supported: {', '.join(SUPPORTED_TIMEFRAMES)}"
        ) from None


def bucket_start(ts: int, timeframe: str) -> int:
    """First nanosecond of the UTC bucket ``ts`` belongs to.

    Minutes, hours and days floor from the epoch; weeks floor to the preceding
    Monday 00:00 UTC.
    """
    if timeframe == "1w":
        epoch_day = ts // (_NS_PER_SECOND * 86_400)
        monday = epoch_day - ((epoch_day - 4) % 7)  # 1970-01-01 was a Thursday
        return monday * 86_400 * _NS_PER_SECOND
    seconds = timeframe_seconds(timeframe)
    return (ts // (seconds * _NS_PER_SECOND)) * seconds * _NS_PER_SECOND


def aggregate(bars: Sequence[Bar], timeframe: str) -> list[Bar]:
    """One bar per instrument per bucket: open from the first, high/low as extremes,
    close from the last, volume summed, ``ts`` taken from the first bar in the bucket.

    Input order does not matter: bars are grouped by instrument and sorted by time,
    so shuffled input produces the identical list.
    """
    timeframe_seconds(timeframe)  # fail fast on an unknown vocabulary
    grouped: dict[object, list[Bar]] = {}
    for bar in bars:
        grouped.setdefault(bar.instrument_id, []).append(bar)

    out: list[Bar] = []
    for instrument_id, series in sorted(
        grouped.items(), key=lambda item: (item[0].symbol, item[0].exchange)
    ):
        series = sorted(series, key=lambda bar: bar.ts)
        current: list[Bar] = []
        bucket = 0
        for bar in series:
            start = bucket_start(bar.ts, timeframe)
            if current and start != bucket:
                out.append(_merge(current))
                current = []
            bucket = start
            current.append(bar)
        if current:
            out.append(_merge(current))
    return out


def _merge(series: list[Bar]) -> Bar:
    first, last = series[0], series[-1]
    return Bar(
        first.instrument_id,
        first.ts,
        first.open,
        max(bar.high for bar in series),
        min(bar.low for bar in series),
        last.close,
        sum(bar.volume for bar in series),
    )
