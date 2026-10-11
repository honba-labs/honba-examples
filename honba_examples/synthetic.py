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
    elif isinstance(ts, date):
        ts_ns = int(datetime.combine(ts, datetime.min.time()).timestamp() * 1_000_000_000)
    elif isinstance(ts, int):
        ts_ns = ts
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


def load_real_bars(
    symbol: str,
    exchange: str = "NSE",
    start: date | datetime | None = None,
    end: date | datetime | None = None,
    timeframe: str = "1D",
    limit: int | None = None,
) -> list[Bar]:
    """Load real historical bars from the honba-data lakehouse, falling back to empty list."""
    try:
        from honba.screener.store import DateInterval, ParquetBarStore, find_data_root

        store = ParquetBarStore(find_data_root())
        start_d = start.date() if isinstance(start, datetime) else (start or date(2020, 1, 1))
        end_d = end.date() if isinstance(end, datetime) else (end or date(2026, 12, 31))
        bars = store.read(InstrumentId(symbol, exchange), timeframe, DateInterval(start_d, end_d))
        if limit is not None and len(bars) > limit:
            return bars[-limit:]
        return bars
    except (OSError, RuntimeError, ValueError, KeyError):
        return []


def get_bars(symbol: str = "RELIANCE", count: int = 60, exchange: str = "NSE") -> list[Bar]:
    """Return real bars from honba-data lakehouse if available, falling back to synthetic bars."""
    real = load_real_bars(symbol, exchange=exchange, limit=count)
    if len(real) >= min(count, 10):
        return real
    start_d = date(2026, 1, 1)
    days = weekdays(start_d, start_d + timedelta(days=count * 2))[:count]
    return [
        bar(
            d,
            2500.0 + i * 0.5,
            2501.0 + i * 0.5,
            2499.0 + i * 0.5,
            2500.5 + i * 0.5,
            symbol=symbol,
        )
        for i, d in enumerate(days)
    ]
