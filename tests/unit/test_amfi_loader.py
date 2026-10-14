"""tests/unit/test_amfi_loader.py: AmfiNavLoader reads back the NAV dates it was given."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from honba.screener.coverage import CoverageRecord, CoverageStatus, DateInterval
from honba.screener.store import ParquetBarStore

from honba_examples.amfi import EXCHANGE, AmfiNavLoader, NavRow, nav_bars

CODE = "120503"
DAYS = [dt.date(2025, 6, 2), dt.date(2025, 6, 3), dt.date(2025, 6, 4), dt.date(2025, 6, 5)]


@pytest.fixture
def loader(tmp_path: Path) -> AmfiNavLoader:
    rows = [NavRow(CODE, "fund", 100.0 + i, d) for i, d in enumerate(DAYS)]
    ParquetBarStore(tmp_path).append(
        CoverageRecord(
            exchange=EXCHANGE,
            symbol=CODE,
            timeframe="1D",
            interval=DateInterval(DAYS[0], DAYS[-1] + dt.timedelta(days=1)),
            status=CoverageStatus.FINAL,
            source="test",
            row_count=len(rows),
        ),
        nav_bars(rows),
    )
    return AmfiNavLoader(tmp_path)


def test_load_returns_the_publication_dates(loader: AmfiNavLoader) -> None:
    points = loader.load(CODE, start=DAYS[0], end=DAYS[-1])
    assert [p.date for p in points][:3] == DAYS[:3]
    assert [p.nav for p in points][:3] == [100.0, 101.0, 102.0]


def test_end_date_is_inclusive(loader: AmfiNavLoader) -> None:
    """The scripts' ``--end`` is a last day to include; the store interval is half-open."""
    points = loader.load(CODE, start=DAYS[0], end=DAYS[-1])
    assert [p.date for p in points] == DAYS


def test_latest_nav_is_the_last_publication(loader: AmfiNavLoader) -> None:
    latest = loader.latest_nav(CODE)
    assert latest is not None
    assert (latest.date, latest.nav) == (DAYS[-1], 103.0)
