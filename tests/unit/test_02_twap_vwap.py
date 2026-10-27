"""tests/unit/test_02_twap_vwap: TWAP/VWAP execution slicers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from honba_examples.options import freeze_limit_qty

_PATH = Path(__file__).resolve().parents[2] / "examples" / "stocksharp" / "02_twap_vwap.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("twap_vwap", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _qtys(result) -> list[int]:
    return [c["quantity"] for c in result["children"]]


def test_twap_slices_within_freeze_limit(example) -> None:
    result = example.run(scheme="twap", parent_qty=5000, slices=75, seed=42)
    qtys = _qtys(result)
    assert len(qtys) == 75
    assert sum(qtys) == 5000
    assert max(qtys) <= freeze_limit_qty()  # 1,800 NIFTY contracts
    assert result["verification"]["all_within_limit"] is True
    assert result["verification"]["sum_equals_parent"] is True
    # TWAP: near-equal children (largest remainder distribution).
    assert max(qtys) - min(qtys) <= 1


def test_vwap_slices_within_freeze_limit(example) -> None:
    result = example.run(scheme="vwap", parent_qty=5000, slices=75, seed=42)
    qtys = _qtys(result)
    assert sum(qtys) == 5000
    assert max(qtys) <= freeze_limit_qty()
    assert result["verification"]["all_within_limit"] is True
    # VWAP: children differ (volume-proportional).
    assert max(qtys) > min(qtys)


def test_strict_freeze_limit(example) -> None:
    result = example.run(scheme="twap", parent_qty=5000, slices=10, freeze_limit=900)
    assert result["config"]["freeze_limit"] == 900
    assert max(_qtys(result)) <= 900


def test_freeze_limit_violation_raises(example) -> None:
    with pytest.raises(ValueError):
        example.run(scheme="twap", parent_qty=5000, slices=5, freeze_limit=900)


def test_invalid_scheme_raises(example) -> None:
    with pytest.raises(ValueError):
        example.run(scheme="iceberg", parent_qty=100, slices=5)


def test_child_times_are_in_session_and_jittered(example) -> None:
    result = example.run(scheme="twap", parent_qty=1000, slices=10, seed=42)
    times = [c["jittered_time"] for c in result["children"]]
    assert all("09:15" <= t <= "15:30" for t in times)
    # scheduled times are exactly 5 minutes apart.
    scheduled = [c["scheduled_time"] for c in result["children"]]
    assert scheduled == sorted(scheduled)


def test_deterministic_across_two_runs(example) -> None:
    first = json.dumps(example.run(seed=11), sort_keys=True, default=str)
    second = json.dumps(example.run(seed=11), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys) -> None:
    out = tmp_path / "slices.json"
    assert example.main(["--out", str(out)]) == 0
    assert json.loads(out.read_text()) == json.loads(capsys.readouterr().out)


def test_main_happy_path_returns_zero(example) -> None:
    assert example.main([]) == 0
