"""honba_examples.bars: UTC bucketing and OHLC aggregation rules."""

from __future__ import annotations

import datetime as dt

import pytest
from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId

from honba_examples.bars import SUPPORTED_TIMEFRAMES, aggregate, bucket_start, timeframe_seconds
from tests.synthetic import bar, session_ts, weekdays

A = InstrumentId("AAA", "NSE")
B = InstrumentId("BBB", "NSE")


def ts(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    moment = dt.datetime(year, month, day, hour, minute, tzinfo=dt.timezone.utc)
    return int(moment.timestamp()) * 1_000_000_000


def test_unknown_timeframe_names_the_supported_vocabulary():
    with pytest.raises(ValueError, match="1w"):
        timeframe_seconds("2h")
    assert timeframe_seconds("1m") == 60
    assert timeframe_seconds("1d") == 86_400
    assert "1w" in SUPPORTED_TIMEFRAMES


def test_bucket_start_floors_to_the_boundary():
    inside = ts(2026, 6, 3, 10, 17)
    assert bucket_start(inside, "5m") == ts(2026, 6, 3, 10, 15)
    assert bucket_start(inside, "1h") == ts(2026, 6, 3, 10)
    assert bucket_start(inside, "1d") == ts(2026, 6, 3)
    assert bucket_start(ts(2026, 6, 3, 10, 15), "5m") == ts(2026, 6, 3, 10, 15)


def test_bucket_start_puts_weeks_on_monday():
    wednesday = ts(2026, 6, 3, 10, 0)
    assert bucket_start(wednesday, "1w") == ts(2026, 6, 1)
    assert bucket_start(ts(2026, 6, 1), "1w") == ts(2026, 6, 1)
    assert bucket_start(ts(2026, 5, 31, 23, 59), "1w") == ts(2026, 5, 25)


def test_aggregate_takes_open_first_close_last_and_sums_volume():
    one_minute = [
        Bar(A, ts(2026, 6, 3, 10, 15), 100.0, 105.0, 99.0, 104.0, 10.0),
        Bar(A, ts(2026, 6, 3, 10, 16), 104.0, 108.0, 103.0, 106.0, 20.0),
        Bar(A, ts(2026, 6, 3, 10, 17), 106.0, 107.0, 95.0, 96.0, 30.0),
    ]
    (five_minute,) = aggregate(one_minute, "5m")
    assert five_minute.instrument_id == A
    assert five_minute.ts == ts(2026, 6, 3, 10, 15)  # stamped at the first bar
    assert (five_minute.open, five_minute.high, five_minute.low, five_minute.close) == (
        100.0,
        108.0,
        95.0,
        96.0,
    )
    assert five_minute.volume == 60.0


def test_aggregate_splits_on_the_boundary_not_before_it():
    last_of_bucket = Bar(A, ts(2026, 6, 3, 10, 19), 1.0, 1.0, 1.0, 1.0, 1.0)
    first_of_next = Bar(A, ts(2026, 6, 3, 10, 20), 2.0, 2.0, 2.0, 2.0, 1.0)
    buckets = aggregate([last_of_bucket, first_of_next], "5m")
    assert [b.ts for b in buckets] == [ts(2026, 6, 3, 10, 19), ts(2026, 6, 3, 10, 20)]


def test_daily_bars_to_weeks_span_the_boundary_once():
    days = weekdays(dt.date(2026, 5, 25), 10)  # two ISO weeks, Monday-start
    weekly = aggregate(
        [bar("AAA", d, 100.0 + i, close=101.0 + i) for i, d in enumerate(days)], "1w"
    )
    assert [b.ts for b in weekly] == [session_ts(days[0]), session_ts(days[5])]
    assert weekly[0].open == 100.0
    assert weekly[0].close == 105.0  # Friday of the first week
    assert weekly[1].open == 105.0
    assert weekly[1].volume == 5_000.0


def test_instruments_never_merge():
    bars = [
        Bar(A, ts(2026, 6, 3, 10, 15), 1.0, 1.0, 1.0, 1.0, 1.0),
        Bar(B, ts(2026, 6, 3, 10, 15), 2.0, 2.0, 2.0, 2.0, 1.0),
    ]
    out = aggregate(bars, "5m")
    assert [b.instrument_id for b in out] == [A, B]
    assert out[0].close == 1.0 and out[1].close == 2.0


def test_input_order_does_not_change_the_result():
    days = weekdays(dt.date(2026, 6, 1), 7)
    bars = [bar("AAA", d, 100.0 + i) for i, d in enumerate(days)]
    assert aggregate(bars, "1d") == aggregate(list(reversed(bars)), "1d")


def test_empty_input_aggregates_to_nothing():
    assert aggregate([], "5m") == []
