"""backtesting/05_monte_carlo: start-date Monte Carlo on synthetic data."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "examples" / "backtesting" / "05_monte_carlo.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("monte_carlo", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_returns_base_and_stress(example):
    result = example.run(use_synthetic=True, offsets=(0, 5, 10), fast=5, slow=20)
    assert "config" in result
    assert "base" in result
    assert "summary" in result

    base = result["base"]
    assert "offsets" in base
    assert "final_equities" in base
    assert "max_drawdown_pcts" in base
    assert "mean_final_equity" in base
    assert "cv_final_equity" in base
    assert "drawdown_spread" in base
    assert "checks" in base
    assert "passed" in base
    assert len(base["offsets"]) == 3


def test_stress_test_when_requested(example):
    result = example.run(use_synthetic=True, offsets=(0, 5, 10), shock="2x", fast=5, slow=20)
    assert "stress" in result
    assert result["stress"] is not None
    stress = result["stress"]
    assert "shock_multiplier" in stress
    assert stress["shock_multiplier"] == 2.0
    assert "note" in stress


def test_deterministic_across_two_runs(example):
    import json as _json

    first = _json.dumps(
        example.run(use_synthetic=True, offsets=(0, 5, 10)),
        sort_keys=True,
        default=str,
    )
    second = _json.dumps(
        example.run(use_synthetic=True, offsets=(0, 5, 10)),
        sort_keys=True,
        default=str,
    )
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "monte_carlo.json"
    assert example.main(["--out", str(out), "--synthetic", "--offsets", "0", "5", "10"]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    stdout_json = json.loads(stdout)
    assert file_json == stdout_json


def test_main_happy_path_returns_zero(example, capsys, tmp_path):
    out = tmp_path / "monte_carlo.json"
    assert example.main(["--out", str(out), "--synthetic", "--offsets", "0", "5", "10"]) == 0
    stdout = capsys.readouterr().out
    assert "Start-date Monte Carlo" in stdout


def test_cv_and_drawdown_spread_computed(example):
    result = example.run(use_synthetic=True, offsets=(0, 5, 10, 20, 30), fast=5, slow=20)
    base = result["base"]
    # CV should be non-negative
    assert base["cv_final_equity"] >= 0
    # Drawdown spread should be non-negative
    assert base["drawdown_spread"] >= 0
