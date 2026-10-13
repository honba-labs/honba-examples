"""Duration parsing and rebalance-session scheduling."""

from __future__ import annotations

import datetime as dt


def parse_duration_to_days(
    val: str | int | None,
    *,
    start_date: dt.date | None = None,
) -> int | None:
    """Parse duration specified as string ('180d', '6m', '1y', ISO date) or integer days."""
    if val is None:
        return None
    if isinstance(val, int):
        if val < 0:
            raise ValueError(f"duration days must be non-negative, got {val}")
        return val
    s = str(val).strip()
    if not s:
        return None

    try:
        d = dt.date.fromisoformat(s)
        if start_date is not None:
            return max(0, (d - start_date).days)
    except ValueError:
        pass

    s_lower = s.lower()
    for suffix in ("days", "day", "d"):
        if s_lower.endswith(suffix):
            return int(s_lower[: -len(suffix)].strip())
    for suffix in ("months", "month", "mo", "m"):
        if s_lower.endswith(suffix):
            return int(s_lower[: -len(suffix)].strip()) * 30
    for suffix in ("years", "year", "yr", "y"):
        if s_lower.endswith(suffix):
            return int(s_lower[: -len(suffix)].strip()) * 365

    return int(s_lower)


def rebalance_sessions(
    calendar: list[dt.date], start: dt.date, rebalance_days: int
) -> set[dt.date]:
    """Map each ``start + k*rebalance_days`` (k >= 1) to the first session on/after it."""
    sessions: set[dt.date] = set()
    scheduled = start + dt.timedelta(days=rebalance_days)
    i = 0
    while scheduled <= calendar[-1]:
        while calendar[i] < scheduled:
            i += 1
        if calendar[i] != calendar[0]:
            sessions.add(calendar[i])
        scheduled += dt.timedelta(days=rebalance_days)
    return sessions
