"""tests/unit/test_sip_helpers.py: allocation weights, eval-window resolution and report writing."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from honba_examples.artifact import write_json_report
from honba_examples.screening.dispatch import symbol_stub
from honba_examples.screening.paths import resolve_eval_window
from honba_examples.sip import allocation_weights

CANDS = [
    {"symbol": "A", "pct_from_high": -1.0, "mb_score": 30.0},
    {"symbol": "B", "pct_from_high": -4.0, "margin_of_safety_pct": 10.0},
]


def test_allocation_weights_models():
    eq = allocation_weights("equal", CANDS)
    assert eq == {"A": 0.5, "B": 0.5}
    near = allocation_weights("52w-ratio", CANDS)
    assert near["A"] == pytest.approx(0.8) and sum(near.values()) == pytest.approx(1.0)
    score = allocation_weights("score", CANDS)
    assert score["A"] == pytest.approx(0.75)
    assert allocation_weights("equal", []) == {}
    assert allocation_weights("bogus", CANDS) == {}


def test_resolve_eval_window_snaps_and_falls_back():
    latest = dt.date(2025, 1, 10)
    notes: list[str] = []
    asof, end = resolve_eval_window(
        "2030-01-01", dt.date(2023, 1, 1), None, dt.date(2026, 12, 31), latest, notes.append
    )
    assert asof == latest and end == latest and len(notes) == 1
    asof, end = resolve_eval_window(None, dt.date(2023, 1, 1), "2024-05-01", None, latest)
    assert (asof, end) == (dt.date(2023, 1, 1), dt.date(2024, 5, 1))


def test_resolve_eval_window_rejects_bad_dates():
    with pytest.raises(ValueError, match="--asof"):
        resolve_eval_window("xx", dt.date(2023, 1, 1), None, None, dt.date(2025, 1, 1))
    with pytest.raises(ValueError, match="--end-date"):
        resolve_eval_window(None, dt.date(2023, 1, 1), "yy", None, dt.date(2025, 1, 1))


def test_write_json_report_and_stub(tmp_path):
    target = tmp_path / "a" / "r.json"
    write_json_report(target, {"k": 1})
    assert json.loads(target.read_text()) == {"k": 1}
    assert symbol_stub("TCS", dt.date(2023, 1, 1))["asof_date"] == "2023-01-01"
