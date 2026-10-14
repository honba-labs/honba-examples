"""Small SQL fragments shared by the point-in-time screeners."""

from __future__ import annotations

import datetime as dt

__all__ = ["DEFAULT_MAX_STALE_DAYS", "stale_price_sql"]

#: A symbol whose last bar is older than this many calendar days before ``--asof`` is skipped.
#: 10 days spans a long exchange holiday run plus a short trading halt; anything older is almost
#: certainly delisted, suspended or otherwise untradeable.
DEFAULT_MAX_STALE_DAYS = 10


def stale_price_sql(asof: dt.date, max_stale_days: int | None, column: str) -> str:
    """``AND <column> >= <asof - max_stale_days>`` or ``""`` when the check is disabled.

    ``None`` or a value ``<= 0`` disables the recency check (last bar at or before ``asof``
    qualifies however old it is).
    """
    if max_stale_days is None or max_stale_days <= 0:
        return ""
    floor = asof - dt.timedelta(days=max_stale_days)
    return f"AND {column} >= '{floor.isoformat()}'"
