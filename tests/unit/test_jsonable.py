"""jsonable: domain enums, sets, tuples and dataclasses become deterministic JSON."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from honba_examples.jsonable import jsonable


class Side(Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class Nested:
    flag: bool = True
    when: dt.date = dt.date(2026, 6, 1)


@dataclass(frozen=True)
class Report:
    name: str
    side: Side = Side.BUY
    tags: frozenset[str] = field(default_factory=lambda: frozenset({"b", "a"}))
    levels: tuple[float, ...] = (1.5, 2.5)
    nested: Nested = field(default_factory=Nested)


def test_enum_becomes_its_value():
    assert jsonable(Side.BUY) == "buy"


def test_unordered_sets_are_sorted_and_ordered_sequences_are_not():
    assert jsonable(frozenset({"z", "a"})) == ["a", "z"]
    assert jsonable(("z", "a")) == ["z", "a"]
    assert jsonable([3, 1]) == [3, 1]


def test_dataclass_becomes_a_dict_with_declared_field_order():
    result = jsonable(Report(name="x"))
    assert list(result) == ["name", "side", "tags", "levels", "nested"]
    assert result == {
        "name": "x",
        "side": "buy",
        "tags": ["a", "b"],
        "levels": [1.5, 2.5],
        "nested": {"flag": True, "when": "2026-06-01"},
    }


def test_dates_and_times_become_iso_strings():
    assert jsonable(dt.datetime(2026, 6, 1, 9, 15, tzinfo=dt.timezone.utc)) == (
        "2026-06-01T09:15:00+00:00"
    )
    assert jsonable(dt.time(9, 15)) == "09:15:00"


def test_dicts_recurse_into_their_values():
    assert jsonable({"when": dt.date(2026, 6, 1), "side": Side.SELL}) == {
        "when": "2026-06-01",
        "side": "sell",
    }


def test_primitives_pass_through_and_paths_become_strings():
    assert jsonable(None) is None
    assert jsonable(True) is True
    assert jsonable(7) == 7
    assert jsonable(1.25) == 1.25
    assert jsonable("x") == "x"
    assert jsonable(Path("a/b")) == "a/b"


def test_result_is_json_serialisable_and_stable():
    first = json.dumps(jsonable(Report(name="x")), sort_keys=True)
    second = json.dumps(jsonable(Report(name="x")), sort_keys=True)
    assert first == second
