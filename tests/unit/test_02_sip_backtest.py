"""tests/unit/test_02_sip_backtest.py: a SIP on a non-publication day buys the next NAV."""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import pytest
from honba.screener.coverage import CoverageRecord, CoverageStatus, DateInterval
from honba.screener.store import ParquetBarStore

from honba_examples.amfi import EXCHANGE, NavRow, nav_bars

_PATH = Path(__file__).resolve().parents[2] / "mutual_funds" / "02_sip_backtest.py"
CODE = "120503"
# 2025-06-07 is a Saturday (no NAV); 2025-06-09 is the next publication.
NAVS = {
    dt.date(2025, 6, 5): 100.0,
    dt.date(2025, 6, 6): 110.0,
    dt.date(2025, 6, 9): 120.0,
    dt.date(2025, 6, 10): 130.0,
}


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("sip_backtest", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(tmp_path: Path, navs: dict[dt.date, float]) -> Path:
    rows = [NavRow(CODE, "fund", nav, d) for d, nav in sorted(navs.items())]
    first, last = rows[0].date, rows[-1].date
    ParquetBarStore(tmp_path).append(
        CoverageRecord(
            exchange=EXCHANGE,
            symbol=CODE,
            timeframe="1D",
            interval=DateInterval(first, last + dt.timedelta(days=1)),
            status=CoverageStatus.FINAL,
            source="test",
            row_count=len(rows),
        ),
        nav_bars(rows),
    )
    return tmp_path


def test_non_publication_day_buys_the_next_nav_not_a_past_one(example, tmp_path) -> None:
    data = _write(tmp_path, NAVS)
    out = example.run(
        scheme=CODE,
        monthly=1200.0,
        start=dt.date(2025, 6, 1),
        end=dt.date(2025, 6, 10),
        sip_day=7,
        data_dir=data,
    )
    (leg,) = out["legs"]
    assert leg["sip_date"] == "2025-06-07"
    assert leg["effective_date"] == "2025-06-09"  # Saturday order is processed Monday
    assert leg["nav"] == 120.0
    assert leg["units"] == pytest.approx(10.0)


def test_sip_with_no_later_nav_is_skipped_not_filled_at_a_stale_price(example, tmp_path) -> None:
    data = _write(tmp_path, {dt.date(2025, 6, 5): 100.0, dt.date(2025, 6, 6): 110.0})
    out = example.run(
        scheme=CODE,
        monthly=1000.0,
        start=dt.date(2025, 6, 1),
        end=dt.date(2025, 6, 10),
        sip_day=7,
        data_dir=data,
    )
    assert out["legs"] == []
    assert out["summary"]["sip_count"] == 0
    assert out["skipped_sip_dates"] == ["2025-06-07"]
