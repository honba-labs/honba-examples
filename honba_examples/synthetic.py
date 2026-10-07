"""Synthetic OHLCV helpers used by examples and unit tests.

These mirror the shapes expected by the replay/backtest helpers and avoid
pulling example code from the tests tree.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId


def bar(
    ts: int | datetime | date,
    o: float,
    h: float,
    l: float,
    c: float,
    v: float = 0,
    symbol: str = "AAA",
    close: float | None = None,
) -> Bar:
    """Create a :class:`~honba.domain.bar.Bar` with a minor-unit close by default."""
    if close is not None:
        c = close
    if isinstance(ts, datetime):
        ts_ns = int(ts.timestamp() * 1_000_000_000)
    elif isinstance(ts, date) and not isinstance(ts, datetime):
        ts_ns = int(datetime.combine(ts, datetime.min.time()).timestamp() * 1_000_000_000)
    else:
        ts_ns = int(ts)

    inst = InstrumentId(symbol=symbol)
    close_minor = round(c * 100.0)
    open_minor = round(o * 100.0)
    high_minor = round(h * 100.0)
    low_minor = round(l * 100.0)
    vol = round(float(v))

    return Bar(
        instrument_id=inst,
        ts=ts_ns,
        open=open_minor,
        high=high_minor,
        low=low_minor,
        close=close_minor,
        volume=vol,
    )


def weekdays(start: date, end: date) -> Sequence[date]:
    """Return trading days (Mon–Fri) between start and end inclusive."""
    days: list[date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days
