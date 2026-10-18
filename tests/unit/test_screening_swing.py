"""Unit: the swing-trading "A+ setup" screener (``honba_examples.screening.swing``)."""

from __future__ import annotations

import datetime as dt

from honba_examples.screening import screen_swing_setups
from tests.unit.conftest import SCREEN_ASOF

STALE_LAST = SCREEN_ASOF - dt.timedelta(days=40)

# A 90-session flat base (closes pinned at 100) that breaks out hard on the last session, with a
# matching volume surge -- clears the breakout pattern gate (new 20-day high + >=1.5x volume) and,
# with a flat peer in the universe, scores comfortably into the A+ band (grade >= 80).
_BASE_N = 90


def _flat_then_breakout(jump: float, n: int = _BASE_N) -> tuple[list[float], list[float]]:
    closes = [100.0] * (n - 1) + [jump]
    volumes = [1e6] * (n - 1) + [3e6]
    return closes, volumes


def _flat_peer(n: int = _BASE_N) -> list[float]:
    return [100.0] * n


def _gentle_uptrend(n: int = _BASE_N) -> tuple[list[float], list[float]]:
    closes = [100.0 * 1.002**i for i in range(n)]
    lows = [c * 0.995 for c in closes]
    return closes, lows


def _downtrend(n: int = _BASE_N) -> list[float]:
    return [100.0 * 0.996**i for i in range(n)]


def test_swing_breakout_scores_a_plus_and_appears_in_output(catalog_dir, add_series) -> None:
    closes, volumes = _flat_then_breakout(118.0)
    add_series("BRKOUT", closes, volumes=volumes)
    add_series("FLATPEER", _flat_peer())

    rows = screen_swing_setups(catalog_dir, SCREEN_ASOF, universe="all")
    by_symbol = {r["symbol"]: r for r in rows}

    assert "BRKOUT" in by_symbol  # appears in the default (grade >= A+) watchlist
    row = by_symbol["BRKOUT"]
    assert row["grade"] == "A+"
    assert row["score"] >= 80.0
    assert "breakout" in row["setup"]
    assert row["rs_excess"] is not None and row["rs_excess"] > 0
    # the symbols-format stream is just the symbol column of each candidate
    assert {r["symbol"] for r in rows} == {"BRKOUT"}


def test_swing_below_50dma_excluded_by_trend_gate(catalog_dir, add_series) -> None:
    add_series("DOWNTREND", _downtrend())
    add_series("FLATPEER", _flat_peer())

    rows = screen_swing_setups(catalog_dir, SCREEN_ASOF, universe="all")
    assert "DOWNTREND" not in {r["symbol"] for r in rows}


def test_swing_tight_range_excluded_by_atr_gate(catalog_dir, add_series) -> None:
    closes, lows = _gentle_uptrend()
    add_series("TIGHT", closes, lows=lows)
    add_series("FLATPEER", _flat_peer())

    # At the default --min-atr-pct (1.0) this name clears every one of the 8 gates (healthy
    # trend + pullback pattern + positive RS), though its low score grades it "C" -- use
    # min_grade="C" to see past the (unrelated) grade filter and isolate the ATR gate itself.
    # Raising --min-atr-pct above its actual ATR% then excludes it on the volatility gate alone.
    loose = {
        r["symbol"]
        for r in screen_swing_setups(catalog_dir, SCREEN_ASOF, universe="all", min_grade="C")
    }
    assert "TIGHT" in loose

    strict = {
        r["symbol"]
        for r in screen_swing_setups(
            catalog_dir, SCREEN_ASOF, universe="all", min_grade="C", min_atr_pct=2.0
        )
    }
    assert "TIGHT" not in strict


def test_swing_drops_stale_symbol(catalog_dir, add_series) -> None:
    # LIVE and DEAD are identical breakouts (same pull on the universe's RS average either way);
    # only DEAD's old last bar differs, so only the staleness gate should tell the runs apart.
    closes, volumes = _flat_then_breakout(140.0)
    add_series("LIVE", closes, volumes=volumes)
    add_series("DEAD", closes, volumes=volumes, last_date=STALE_LAST)
    add_series("FLATPEER", _flat_peer())

    default = {r["symbol"] for r in screen_swing_setups(catalog_dir, SCREEN_ASOF, universe="all")}
    assert default == {"LIVE"}

    loose = {
        r["symbol"]
        for r in screen_swing_setups(catalog_dir, SCREEN_ASOF, universe="all", max_stale_days=None)
    }
    assert loose == {"LIVE", "DEAD"}


def test_swing_min_grade_filters_a_grade_candidate(catalog_dir, add_series) -> None:
    # jump=110 (vs. jump=118 above) scores into the A band (>=65, <80), not A+.
    closes, volumes = _flat_then_breakout(110.0)
    add_series("AGRADE", closes, volumes=volumes)
    add_series("FLATPEER", _flat_peer())

    default_rows = screen_swing_setups(catalog_dir, SCREEN_ASOF, universe="all")
    assert {r["symbol"] for r in default_rows} == set()  # default min_grade="A+" excludes it

    a_rows = {
        r["symbol"]: r
        for r in screen_swing_setups(catalog_dir, SCREEN_ASOF, universe="all", min_grade="A")
    }
    assert "AGRADE" in a_rows
    assert a_rows["AGRADE"]["grade"] == "A"
    assert 65.0 <= a_rows["AGRADE"]["score"] < 80.0

    b_rows = {
        r["symbol"]
        for r in screen_swing_setups(catalog_dir, SCREEN_ASOF, universe="all", min_grade="B")
    }
    assert "AGRADE" in b_rows
