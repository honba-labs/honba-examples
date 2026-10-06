"""honba_examples.amfi: parse AMFI's daily NAVAll.txt publication into NAV rows and bars.

NAVAll.txt is the end-of-day NAV sheet the Association of Mutual Funds in India publishes
every working day: one UTF-8 file per publication date, semicolon-separated, with an
``Applic Date:`` header and four fields per line (scheme code, scheme name, NAV, date).
Honba's own loader (``honba/data/loaders/amfi.py``) is an empty placeholder, so this module
is where the format is written down and tested.

Parsing is lenient where values are merely missing and loud where the structure is damaged:
a NAV that is not a positive finite number (``-``, empty, ``nan``) drops the row and records
why, while a line that is not four fields - or a date outside ``%d-%b-%Y`` - raises
``ValueError`` naming the offending line. A NAV is a single price for the whole day, so the
bar it becomes carries no range: ``open == high == low == close == nav``, ``volume == 0``,
stamped at the 09:15 IST session open (03:45 UTC) the Parquet store uses for daily bars.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId

__all__ = ["EXCHANGE", "NavRow", "NavSkip", "nav_bars", "parse_navall"]

EXCHANGE = "AMFI"
"""Venue the series is booked under: NAVs are published, not traded, so they name the publisher."""

_DATE_FORMAT = "%d-%b-%Y"
_FIELDS = 4
_EPOCH = dt.date(1970, 1, 1)
_NS_PER_DAY = 86_400 * 1_000_000_000
_SESSION_OPEN_NS = (3 * 3600 + 45 * 60) * 1_000_000_000  # 03:45 UTC == 09:15 IST


@dataclass(frozen=True)
class NavRow:
    """One scheme's NAV on one publication date."""

    code: str
    name: str
    nav: float
    date: dt.date


@dataclass(frozen=True)
class NavSkip:
    """A row dropped because its NAV is missing or not a positive finite number."""

    line: int
    code: str
    reason: str


def parse_navall(text: str, skipped: list[NavSkip] | None = None) -> list[NavRow]:
    """Rows of one NAVAll.txt publication, sorted by ``(code, date)``.

    Blank lines and the ``Applic Date:`` header are not data. A row whose NAV is not a
    positive finite number is dropped and, when ``skipped`` is given, collected there with
    its line number, scheme code and reason - that is the count of rows the publication
    lost to missing values. A row that is not ``;``-separated into exactly four fields
    raises ``ValueError`` naming the offending line, and so does a date that is not in
    ``%d-%b-%Y`` format: structure is never repaired silently, values are never fatal.
    """
    rows: list[NavRow] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.lower().startswith("applic date:"):
            continue
        fields = [field.strip() for field in line.split(";")]
        if len(fields) != _FIELDS:
            raise ValueError(
                f"line {line_no}: expected {_FIELDS} ';'-separated fields, "
                f"got {len(fields)}: {line!r}"
            )
        code, name, nav_text, date_text = fields
        nav = _positive_float(nav_text)
        if nav is None:
            if skipped is not None:
                skipped.append(
                    NavSkip(
                        line=line_no,
                        code=code,
                        reason=f"NAV {nav_text!r} is not a positive finite number",
                    )
                )
            continue
        try:
            date = _publication_date(date_text)
        except ValueError as exc:
            raise ValueError(
                f"line {line_no}: NAV date {date_text!r} is not in {_DATE_FORMAT} format"
            ) from exc
        rows.append(NavRow(code=code, name=name, nav=nav, date=date))
    rows.sort(key=lambda row: (row.code, row.date))
    return rows


def nav_bars(rows: Sequence[NavRow]) -> list[Bar]:
    """One daily ``Bar`` per row, in the order the rows are given.

    A NAV is the day's only price, so there is no range and no traded size to record:
    open, high, low and close are all the NAV and volume is zero. The bar is stamped at the
    session open (03:45 UTC, i.e. 09:15 IST) of its publication date, the same stamp the
    store holds daily equity bars at, so a NAV series lines up with price series bar for bar.
    """
    return [
        Bar(
            instrument_id=InstrumentId(row.code, EXCHANGE),
            ts=(row.date - _EPOCH).days * _NS_PER_DAY + _SESSION_OPEN_NS,
            open=row.nav,
            high=row.nav,
            low=row.nav,
            close=row.nav,
            volume=0.0,
        )
        for row in rows
    ]


def _publication_date(text: str) -> dt.date:
    """``text`` as a calendar date in AMFI's ``%d-%b-%Y`` format, or ``ValueError``."""
    return dt.datetime.strptime(text, _DATE_FORMAT).date()  # noqa: DTZ007 - date-only parse


def _positive_float(text: str) -> float | None:
    """``text`` as a finite number > 0, or ``None`` when it is missing or not a number."""
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 0 else None
