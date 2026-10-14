"""Shared synthetic OHLCV helpers: deterministic daily bars (no network, no wall clock).

Used by the example scripts and the unit tests alike.
"""

from __future__ import annotations

import datetime as dt
import math
import random

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


def trending_bars(
    symbol: str,
    count: int,
    start: dt.date = dt.date(2026, 1, 1),
    base: float = 2500.0,
    step: float = 0.5,
    exchange: str = "NSE",
) -> list[Bar]:
    """``count`` weekday daily bars from ``start`` whose price ramps by ``step`` per bar."""
    return [
        bar(symbol, d, base + i * step, exchange=exchange)
        for i, d in enumerate(weekdays(start, count))
    ]


def oscillating_bars(
    symbol: str,
    count: int,
    start: dt.date = dt.date(2026, 1, 1),
    base: float = 2500.0,
    amplitude: float = 100.0,
    period: int = 40,
    drift: float = 0.0,
    exchange: str = "NSE",
    noise: float = 0.0,
    seed: int = 0,
) -> list[Bar]:
    """``count`` weekday bars whose close is a sine wave around ``base`` (plus ``drift`` per bar).

    ``noise`` > 0 adds Gaussian noise of that standard deviation (price units) from a private
    ``random.Random(seed)``: still deterministic, but every cycle differs.

    Deterministic and trade-friendly: unlike the ramp, price rises and falls, so RSI leaves 100
    and moving-average strategies enter *and* exit. Each bar opens at the previous close.
    """
    rng = random.Random(seed)
    closes = [
        round(
            base
            + i * drift
            + amplitude * math.sin(2 * math.pi * i / period)
            + (rng.gauss(0.0, noise) if noise else 0.0),
            2,
        )
        for i in range(count)
    ]
    out: list[Bar] = []
    for i, d in enumerate(weekdays(start, count)):
        open_ = closes[i - 1] if i else base
        out.append(bar(symbol, d, open_, closes[i], exchange=exchange))
    return out


def load_real_bars(
    symbol: str,
    exchange: str = "NSE",
    start: dt.date | dt.datetime | None = None,
    end: dt.date | dt.datetime | None = None,
    timeframe: str = "1D",
    limit: int | None = None,
) -> list[Bar]:
    """Load real historical bars from the honba-data lakehouse, falling back to empty list."""
    try:
        from honba.screener.store import DateInterval, ParquetBarStore, find_data_root

        store = ParquetBarStore(find_data_root())
        start_d = start.date() if isinstance(start, dt.datetime) else (start or dt.date(2020, 1, 1))
        end_d = end.date() if isinstance(end, dt.datetime) else (end or dt.date(2026, 12, 31))
        bars = store.read(InstrumentId(symbol, exchange), timeframe, DateInterval(start_d, end_d))
        if limit is not None and len(bars) > limit:
            return bars[-limit:]
        return bars
    except (OSError, RuntimeError, ValueError, KeyError):
        return []


def get_bars_with_source(
    symbol: str = "RELIANCE", count: int = 60, exchange: str = "NSE"
) -> tuple[list[Bar], str]:
    """Like :func:`get_bars` but also says where the bars came from: ``lakehouse`` or ``synthetic``."""
    real = load_real_bars(symbol, exchange=exchange, limit=count)
    if len(real) >= min(count, 10):
        return real, "lakehouse"
    return trending_bars(symbol, count), "synthetic"


def get_bars(symbol: str = "RELIANCE", count: int = 60, exchange: str = "NSE") -> list[Bar]:
    """Return real bars from honba-data lakehouse if available, falling back to synthetic bars."""
    return get_bars_with_source(symbol, count, exchange)[0]
