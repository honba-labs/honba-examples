"""tests/unit/test_03_turtle_breakout: Turtle Donchian breakout with pyramiding."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "examples" / "stocksharp" / "03_turtle_breakout.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("turtle_breakout", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_triggers_breakout_and_pyramid(example) -> None:
    result = example.run(bars=200, seed=42)
    assert set(result) == {"config", "summary"}
    summary = result["summary"]
    assert summary["event_count"] >= 1
    sides = {e["side"] for e in summary["events"]}
    assert "enter_long" in sides or "enter_short" in sides
    assert any(e["side"].startswith("add_") for e in summary["events"])  # pyramiding
    assert any(e["side"].startswith("exit_") for e in summary["events"])
    for e in summary["events"]:
        assert set(e) >= {"bar", "close", "side", "units"}
        assert e["units"] <= 4  # max 4 units pyramid cap


def test_system_2_variant_runs(example) -> None:
    # 55-day entry / 20-day exit (Turtle System 2) is a flag change.
    result = example.run(bars=300, seed=42, entry_window=55, exit_window=20)
    assert result["config"]["entry_window"] == 55
    assert result["summary"]["event_count"] >= 0


def test_deterministic_across_two_runs(example) -> None:
    first = json.dumps(example.run(seed=5), sort_keys=True, default=str)
    second = json.dumps(example.run(seed=5), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys) -> None:
    out = tmp_path / "turtle.json"
    assert example.main(["--out", str(out)]) == 0
    assert json.loads(out.read_text()) == json.loads(capsys.readouterr().out)


def test_main_happy_path_returns_zero(example) -> None:
    assert example.main([]) == 0
