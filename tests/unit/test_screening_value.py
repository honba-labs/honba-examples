"""Unit: value screener 52-week-low window and touch flag (synthetic parquet, no fundamentals)."""

from __future__ import annotations

import pytest

from honba_examples.screening import value
from tests.unit.conftest import SCREEN_ASOF


@pytest.fixture(autouse=True)
def _no_fundamentals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(value, "find_duckdb_path", lambda: None)


def _run(catalog_dir):
    rows = value.screen_value_and_dividend(
        catalog_dir, SCREEN_ASOF, train_years=2, max_pct_from_52w_low=500.0
    )
    return {r["symbol"]: r for r in rows}


def test_52w_low_uses_full_window_and_touch_needs_recent_low(catalog_dir, add_series) -> None:
    n = 300
    # OLDLOW: bottom 100 sessions ago (inside the 252-session window), 10 flat recently.
    old = [50.0 + i * 0.4 if i < 200 else 100.0 for i in range(n)]
    old_lows = list(old)
    old_lows[200] = 40.0  # true 52w low, long before the 30-day lookback
    add_series("OLDLOW", [min(c, 100.0) for c in old], lows=[min(x, 100.0) for x in old_lows])
    # FRESH: makes its 52w low in the last few sessions.
    fresh = [100.0 - i * 0.1 for i in range(n)]
    add_series("FRESH", fresh)
    rows = _run(catalog_dir)
    assert rows["OLDLOW"]["low52"] == pytest.approx(40.0, abs=0.01)  # not the 30-day low
    assert rows["OLDLOW"]["hit_52w_low_recently"] is False
    assert rows["FRESH"]["hit_52w_low_recently"] is True
