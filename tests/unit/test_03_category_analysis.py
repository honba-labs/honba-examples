"""tests/unit/test_03_category_analysis.py: trailing-window CAGRs of the category analysis."""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import pytest

from honba_examples.amfi import NavPoint

_PATH = (
    Path(__file__).resolve().parents[2] / "examples" / "mutual_funds" / "03_category_analysis.py"
)


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("category_analysis", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _series(years: int, early_rate: float, late_rate: float) -> list[NavPoint]:
    """Daily NAVs for ``years`` years: ``early_rate`` p.a. until the last 3 years, then ``late_rate``."""
    start = dt.date(2015, 1, 1)
    n = int(years * 365.25)
    nav = 100.0
    out = []
    for i in range(n + 1):
        d = start + dt.timedelta(days=i)
        out.append(NavPoint(date=d, nav=nav))
        rate = early_rate if i < n - int(3 * 365.25) else late_rate
        nav *= (1 + rate) ** (1 / 365.25)
    return out


def test_three_year_cagr_covers_only_the_trailing_three_years(example) -> None:
    pts = _series(8, early_rate=0.30, late_rate=0.10)
    (cagr,) = example._annual_returns(pts, 3)
    assert cagr == pytest.approx(0.10, abs=0.005)  # not the 8-year blend (~0.2)


def test_five_year_cagr_is_not_the_full_history_cagr(example) -> None:
    pts = _series(8, early_rate=0.30, late_rate=0.10)
    (cagr,) = example._annual_returns(pts, 5)
    # 2 years at 30% then 3 years at 10%
    expected = (1.30**2 * 1.10**3) ** (1 / 5) - 1
    assert cagr == pytest.approx(expected, abs=0.005)


def test_history_shorter_than_the_window_gives_no_cagr(example) -> None:
    assert example._annual_returns(_series(2, 0.1, 0.1), 3) == []
