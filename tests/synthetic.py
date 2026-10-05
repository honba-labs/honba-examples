"""Shared fixtures: deterministic synthetic daily bars (no network, no wall clock)."""

from __future__ import annotations

import datetime as dt

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId

IST_OPEN_UTC = dt.time(3, 45)  # 09:15 IST; Parquet daily bars are stamped at the open


def session_ts(day: dt.date) -> int:
    """Unix-ns timestamp of a daily bar for ``day`` (session open, as the store has it)."""
    moment = dt.datetime.combine(day, IST_OPEN_UTC, tzinfo=dt.timezone.utc)
    return int(moment.timestamp()) * 1_000_000_000


def bar(
    symbol: str,
    day: dt.date,
    open_: float,
    close: float | None = None,
    exchange: str = "NSE",
) -> Bar:
    close = open_ if close is None else close
    return Bar(
        InstrumentId(symbol, exchange),
        session_ts(day),
        open_,
        max(open_, close),
        min(open_, close),
        close,
        1_000.0,
    )


def weekdays(start: dt.date, n: int) -> list[dt.date]:
    """The first ``n`` Monday-Friday dates on or after ``start``."""
    out: list[dt.date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out
