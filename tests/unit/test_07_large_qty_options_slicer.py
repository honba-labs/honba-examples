"""tests/unit/test_07_large_qty_options_slicer: freeze-limit option slicing."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from honba_examples.options import freeze_limit_qty, slice_order, verify_slices

_PATH = (
    Path(__file__).resolve().parents[2] / "examples" / "options" / "07_large_qty_options_slicer.py"
)


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("large_qty_options_slicer", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_freeze_limit_default_offline() -> None:
    # No instrument master reachable offline -> planned NIFTY default.
    assert freeze_limit_qty() == 1800


def test_slice_order_respects_limit() -> None:
    slices = slice_order(5000, 1800)
    assert slices == [1800, 1800, 1400]
    assert all(s <= 1800 for s in slices)
    assert sum(slices) == 5000


def test_slice_order_edge_cases() -> None:
    assert slice_order(1800, 1800) == [1800]
    assert slice_order(1, 1800) == [1]
    with pytest.raises(ValueError):
        slice_order(0, 1800)
    with pytest.raises(ValueError):
        slice_order(5000, 0)


def test_verify_slices_rejects_over_limit() -> None:
    verify_slices([1800, 1800, 1400], 1800)  # OK, no raise
    with pytest.raises(ValueError):
        verify_slices([2000], 1800)


def test_run_verification(example) -> None:
    result = example.run(total_quantity=5000, freeze_limit=1800)
    assert result["config"]["freeze_limit"] == 1800
    assert result["config"]["slicer"] == "honba_examples.options"
    verification = result["verification"]
    assert verification["all_within_limit"] is True
    assert verification["max_slice"] == 1800
    assert verification["sum_equals_parent"] is True
    assert verification["all_lot_multiples"] is True
    assert verification["slices_required"] == 3
    assert sum(s["quantity"] for s in result["slices"]) == 5000


def test_cli_freeze_limit_flag_wins(example) -> None:
    result = example.run(total_quantity=5000, freeze_limit=1000)
    assert result["config"]["freeze_limit"] == 1000
    assert result["config"]["freeze_limit_source"] == "cli"
    assert result["verification"]["max_slice"] == 1000


def test_deterministic_across_two_runs(example) -> None:
    first = json.dumps(example.run(total_quantity=5000), sort_keys=True, default=str)
    second = json.dumps(example.run(total_quantity=5000), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys) -> None:
    out = tmp_path / "slices.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    assert json.loads(out.read_text()) == json.loads(stdout)
    assert json.loads(stdout)["verification"]["all_within_limit"] is True


def test_main_happy_path_returns_zero(example) -> None:
    assert example.main([]) == 0
