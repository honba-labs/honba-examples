"""backtesting/05_monte_carlo: bootstrap equity curves on synthetic data."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "backtesting" / "05_monte_carlo.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("monte_carlo", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_returns_mc_results(example):
    result = example.run(bars=60, paths=50)
    assert "config" in result
    assert "mc" in result
    assert "final_equity" in result["mc"]
    assert "max_drawdown_pct" in result["mc"]
    assert "sharpe" in result["mc"]


def test_mc_results_have_percentiles(example):
    result = example.run(paths=50)
    mc = result["mc"]
    for key in ["final_equity", "max_drawdown_pct", "sharpe"]:
        assert "mean" in mc[key]
        assert "p5" in mc[key]
        assert "p95" in mc[key]


def test_deterministic_across_two_runs(example):
    import json as _json
    first = _json.dumps(example.run(paths=50), sort_keys=True, default=str)
    second = _json.dumps(example.run(paths=50), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "monte_carlo.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    stdout_json = json.loads(stdout)
    assert file_json == stdout_json


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main([]) == 0
    assert "mc" in capsys.readouterr().out