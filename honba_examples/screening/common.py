"""Small SQL fragments shared by the point-in-time screeners."""

from __future__ import annotations

import datetime as dt

__all__ = ["DEFAULT_FISCAL_LAG_DAYS", "DEFAULT_MAX_STALE_DAYS", "fiscal_cutoff", "stale_price_sql"]

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


#: Default reporting-lag proxy, in calendar days. SEBI LODR gives listed companies up to 45 days
#: after a quarter end and 60 days after the financial year end to publish results, so 90 days
#: after the period end is a conservative round bound. It is an APPROXIMATION: the fundamentals
#: tables hold no filing or announcement date, a company can file late, and results can be
#: restated afterwards. It does not guarantee that a figure was public on the as-of date.
DEFAULT_FISCAL_LAG_DAYS = 90


def fiscal_cutoff(asof: dt.date, fiscal_lag_days: int) -> str:
    """ISO date: the latest fiscal period end treated as known on ``asof`` (period end + lag)."""
    return (asof - dt.timedelta(days=max(0, fiscal_lag_days))).isoformat()
