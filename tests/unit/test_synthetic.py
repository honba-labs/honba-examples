"""honba_examples.synthetic.trending_bars: deterministic weekday-only ramp of daily bars."""

from __future__ import annotations

import datetime as dt
from itertools import pairwise

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


def test_oscillating_bars_are_deterministic_and_oscillate() -> None:
    from honba_examples.synthetic import oscillating_bars

    first = oscillating_bars("RELIANCE", 120)
    assert first == oscillating_bars("RELIANCE", 120)
    assert len(first) == 120
    assert all(b.instrument_id.symbol == "RELIANCE" for b in first)
    closes = [b.close for b in first]
    ups = sum(1 for a, b in pairwise(closes) if b > a)
    downs = sum(1 for a, b in pairwise(closes) if b < a)
    assert ups > 20 and downs > 20  # unlike the ramp, price falls as well as rises
    assert oscillating_bars("TCS", 0) == []


def test_oscillating_bars_parameters_and_ohlc_validity() -> None:
    from honba_examples.synthetic import oscillating_bars

    bars = oscillating_bars("TCS", 80, base=100.0, amplitude=10.0, period=20, drift=0.0)
    assert max(b.close for b in bars) <= 110.0 + 1e-6
    assert min(b.close for b in bars) >= 90.0 - 1e-6
    assert all(b.low <= min(b.open, b.close) and b.high >= max(b.open, b.close) for b in bars)


def test_get_bars_with_source_labels_where_the_bars_came_from(monkeypatch) -> None:
    import honba_examples.synthetic as syn

    monkeypatch.setattr(syn, "load_real_bars", lambda *a, **k: [])
    bars, source = syn.get_bars_with_source("RELIANCE", count=12)
    assert source == "synthetic" and bars == trending_bars("RELIANCE", 12)

    real = trending_bars("RELIANCE", 12, base=100.0)
    monkeypatch.setattr(syn, "load_real_bars", lambda *a, **k: real)
    bars, source = syn.get_bars_with_source("RELIANCE", count=12)
    assert source == "lakehouse" and bars == real
    assert syn.get_bars("RELIANCE", count=12) == real
