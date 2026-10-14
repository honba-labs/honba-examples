"""backtesting/06_concurrent_backtest: concurrent parameter sweep on synthetic data."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "backtesting" / "06_concurrent_backtest.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("concurrent_backtest", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_returns_sweep_results(example):
    result = example.run(workers=2)
    assert set(result) == {
        "config",
        "total_combinations",
        "multiple_testing",
        "best_by_sharpe",
        "pareto_frontier",
        "all",
    }
    assert result["total_combinations"] > 0
    assert "fast" in result["best_by_sharpe"]
    assert "slow" in result["best_by_sharpe"]


def test_pareto_frontier_non_empty(example):
    result = example.run(workers=2)
    assert len(result["pareto_frontier"]) > 0


def test_deterministic_across_two_runs(example):
    import json as _json

    first = _json.dumps(example.run(workers=2), sort_keys=True, default=str)
    second = _json.dumps(example.run(workers=2), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "concurrent.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    stdout_json = json.loads(stdout)
    assert file_json == stdout_json


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main([]) == 0
    assert "pareto" in capsys.readouterr().out


def test_default_sweep_discriminates_between_pairs(example):
    result = example.run(workers=2)
    ranked = [r for r in result["all"] if r["status"] == "ok"]
    assert len(ranked) >= 10
    assert len({r["sharpe"] for r in ranked}) > 5  # not one tied value
    best = result["best_by_sharpe"]
    assert best["status"] == "ok"
    assert best["sharpe"] == max(r["sharpe"] for r in ranked)


def test_multiple_testing_note_counts_the_combinations_tried(example):
    result = example.run(workers=2)
    mt = result["multiple_testing"]
    assert mt["combinations_tried"] == result["total_combinations"]
    assert mt["ranked"] + mt["insufficient_trades"] == mt["combinations_tried"]
    assert str(mt["combinations_tried"]) in mt["note"]


def test_too_few_trades_is_reported_not_ranked(example):
    result = example.run(workers=2, min_trades=1000)  # no pair can trade that often
    assert result["best_by_sharpe"] is None
    assert result["pareto_frontier"] == []
    assert all(r["status"] == "insufficient_trades" for r in result["all"])
    assert all(r["sharpe"] is None for r in result["all"])
    assert result["multiple_testing"]["ranked"] == 0
    assert "insufficient" in result["multiple_testing"]["note"]
