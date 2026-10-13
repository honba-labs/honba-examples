"""honba_examples.synthetic.trending_bars: deterministic weekday-only ramp of daily bars."""

from __future__ import annotations

import datetime as dt

from honba_examples.synthetic import bar, trending_bars, weekdays


def test_trending_bars_matches_manual_construction() -> None:
    start = dt.date(2026, 1, 1)
    expected = [bar("RELIANCE", d, 2500.0 + i * 0.5) for i, d in enumerate(weekdays(start, 12))]
    assert trending_bars("RELIANCE", 12) == expected


def test_trending_bars_parameters() -> None:
    bars = trending_bars("TCS", 3, start=dt.date(2026, 1, 2), base=100.0, step=2.0)
    assert [b.close for b in bars] == [100.0, 102.0, 104.0]
    assert all(b.instrument_id.symbol == "TCS" for b in bars)
    assert trending_bars("TCS", 0) == []
