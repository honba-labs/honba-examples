"""strategies/03_position_sizing: three sizing methods on synthetic bars."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "strategies" / "03_position_sizing.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("position_sizing", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_returns_strategy_config_and_result(example):
    result = example.run(bars=20)
    assert set(result) == {"strategy", "config", "result"}
    assert result["strategy"]["name"] == "sma_sizing"
    assert result["config"]["sizing"] == "fixed"


def test_result_contains_fills_and_intents(example):
    result = example.run(bars=100)
    res = result["result"]
    assert "fills" in res
    assert "intents" in res


def test_deterministic_across_two_runs(example):
    import json as _json
    first = _json.dumps(example.run(bars=50), sort_keys=True, default=str)
    second = _json.dumps(example.run(bars=50), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "position_sizing.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    stdout_json = json.loads(stdout)
    assert file_json == stdout_json
    assert file_json["strategy"]["name"] == "sma_sizing"


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main([]) == 0
    assert "sma_sizing" in capsys.readouterr().out