"""08: the settlement default is the core's date-aware cycle; an explicit value wins."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "universes" / "08_alpha30_union_ewr_backtest.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("ex08", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def test_engine_default_depends_on_the_start_date(example) -> None:
    cls = example.Alpha30EWRExample
    assert cls.engine_settlement_days("NSE", as_of=dt.date(2022, 6, 1)) == 2
    assert cls.engine_settlement_days("NSE", as_of=dt.date(2026, 6, 1)) == 1


def test_resolve_prefers_explicit_value(example) -> None:
    ex = example.Alpha30EWRExample()
    ex.start_date = dt.date(2026, 1, 1)
    assert ex.resolve_settlement_days() == 1
    ex.settlement_days = 2  # the old fixed T+2 stays available for what-if runs
    assert ex.resolve_settlement_days() == 2
    ex.settlement_days = 0
    assert ex.resolve_settlement_days() == 0
