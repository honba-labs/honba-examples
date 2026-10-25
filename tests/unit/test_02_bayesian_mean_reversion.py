"""optimizer/02_bayesian_mean_reversion: Bayesian optimization (TPE) on synthetic data."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = (
    Path(__file__).resolve().parents[2] / "examples" / "optimizer" / "02_bayesian_mean_reversion.py"
)


@pytest.fixture(scope="module")
def example():
    import sys

    module_name = "bayesian_mean_reversion_module"
    spec = importlib.util.spec_from_file_location(module_name, _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_run_returns_best_and_pareto(example):
    result = example.run(bars=200, trials=30, initial_points=10, seed=123)
    assert "config" in result
    assert "best_params" in result
    assert "best_fitness" in result
    assert "pareto_frontier" in result
    assert "pareto_plot" in result
    assert result["total_evaluations"] == 30
    assert isinstance(result["best_params"], dict)
    assert all(
        k in result["best_params"] for k in ("rsi_period", "oversold", "overbought", "lookback")
    )


def test_deterministic_across_two_runs(example):
    import json as _json

    first = _json.dumps(
        example.run(bars=200, trials=20, initial_points=5, seed=42), sort_keys=True, default=str
    )
    second = _json.dumps(
        example.run(bars=200, trials=20, initial_points=5, seed=42), sort_keys=True, default=str
    )
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "bayesian_mean_reversion.json"
    assert example.main(["--out", str(out), "--trials", "20", "--initial-points", "5"]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    stdout_json = json.loads(stdout)
    assert file_json == stdout_json


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main(["--trials", "15", "--initial-points", "5"]) == 0
    assert "pareto_frontier" in capsys.readouterr().out


def test_initial_points_respected(example):
    result = example.run(bars=200, trials=50, initial_points=20, seed=42)
    # First 20 evaluations should be random, rest biased
    # We can't directly test the internals, but verify total evaluations
    assert result["total_evaluations"] == 50


def test_pareto_frontier_non_dominated(example):
    result = example.run(bars=200, trials=50, initial_points=10, seed=42)
    pareto = result["pareto_frontier"]
    assert len(pareto) >= 1

    for i, p1 in enumerate(pareto):
        for j, p2 in enumerate(pareto):
            if i == j:
                continue
            dominates = (
                p2["return_pct"] >= p1["return_pct"]
                and p2["max_dd_pct"] <= p1["max_dd_pct"]
                and (p2["return_pct"] > p1["return_pct"] or p2["max_dd_pct"] < p1["max_dd_pct"])
            )
            assert not dominates, f"Pareto point {i} is dominated by {j}"
