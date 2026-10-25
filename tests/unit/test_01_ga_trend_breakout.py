"""optimizer/01_ga_trend_breakout: Genetic Algorithm optimization on synthetic data."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "examples" / "optimizer" / "01_ga_trend_breakout.py"


@pytest.fixture(scope="module")
def example():
    import sys

    module_name = "ga_trend_breakout_module"
    spec = importlib.util.spec_from_file_location(module_name, _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_run_returns_pareto_and_best(example):
    result = example.run(bars=200, generations=5, population=10, seed=123)
    assert "config" in result
    assert "best_params" in result
    assert "pareto_frontier" in result
    assert "pareto_plot" in result
    assert result["total_evaluations"] == 5 * 10  # generations * population
    assert isinstance(result["best_params"], dict)
    assert all(
        k in result["best_params"]
        for k in ("fast", "slow", "rsi_period", "rsi_threshold", "atr_period", "atr_mult")
    )


def test_deterministic_across_two_runs(example):
    import json as _json

    first = _json.dumps(
        example.run(bars=200, generations=5, population=10, seed=42), sort_keys=True, default=str
    )
    second = _json.dumps(
        example.run(bars=200, generations=5, population=10, seed=42), sort_keys=True, default=str
    )
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "ga_trend_breakout.json"
    assert example.main(["--out", str(out), "--generations", "3", "--population", "5"]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    stdout_json = json.loads(stdout)
    assert file_json == stdout_json


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main(["--generations", "2", "--population", "4"]) == 0
    assert "pareto_frontier" in capsys.readouterr().out


def test_pareto_frontier_is_non_dominated(example):
    result = example.run(bars=200, generations=10, population=20, seed=42)
    pareto = result["pareto_frontier"]
    assert len(pareto) >= 1

    # Verify Pareto property: no point dominates another
    for i, p1 in enumerate(pareto):
        for j, p2 in enumerate(pareto):
            if i == j:
                continue
            # p2 dominates p1 if: higher return AND lower drawdown
            dominates = (
                p2["return_pct"] >= p1["return_pct"]
                and p2["max_dd_pct"] <= p1["max_dd_pct"]
                and (p2["return_pct"] > p1["return_pct"] or p2["max_dd_pct"] < p1["max_dd_pct"])
            )
            assert not dominates, f"Pareto point {i} is dominated by {j}"
