"""backtesting/04_walk_forward: walk-forward analysis on synthetic data."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "examples" / "backtesting" / "04_walk_forward.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("walk_forward", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_returns_folds_and_wfe(example):
    result = example.run(
        use_synthetic=True, train_months=12, test_months=3, n_folds=4, fast=5, slow=20
    )
    assert "config" in result
    assert "gates" in result
    assert "wfe" in result
    assert "folds" in result
    assert len(result["folds"]) == 4

    for fold in result["folds"]:
        assert "in_sample" in fold
        assert "out_of_sample" in fold
        assert "oos_is_ratio" in fold
        assert fold["in_sample"]["sharpe"] >= 0
        assert fold["out_of_sample"]["sharpe"] >= 0


def test_wfe_structure(example):
    result = example.run(use_synthetic=True, train_months=12, test_months=3, n_folds=4)
    wfe = result["wfe"]
    assert "mean_in_sample_sharpe" in wfe
    assert "mean_out_of_sample_sharpe" in wfe
    assert "walk_forward_efficiency" in wfe
    assert "wfe_passed" in wfe


def test_gates_structure(example):
    result = example.run(use_synthetic=True, train_months=12, test_months=3, n_folds=4)
    gates = result["gates"]
    assert "checks" in gates
    assert "passed" in gates
    assert set(gates["checks"].keys()) == {"mean_oos_sharpe", "oos_sharpe_std", "profitable_folds"}


def test_deterministic_across_two_runs(example):
    import json as _json

    first = _json.dumps(
        example.run(use_synthetic=True, train_months=12, test_months=3, n_folds=4),
        sort_keys=True,
        default=str,
    )
    second = _json.dumps(
        example.run(use_synthetic=True, train_months=12, test_months=3, n_folds=4),
        sort_keys=True,
        default=str,
    )
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "walk_forward.json"
    assert (
        example.main(
            [
                "--out",
                str(out),
                "--synthetic",
                "--train-months",
                "12",
                "--test-months",
                "3",
                "--n-folds",
                "4",
            ]
        )
        == 0
    )
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    stdout_json = json.loads(stdout)
    assert file_json == stdout_json


def test_main_happy_path_returns_zero(example, capsys):
    assert (
        example.main(
            ["--synthetic", "--train-months", "12", "--test-months", "3", "--n-folds", "4"]
        )
        == 0
    )
    assert "Walk-forward" in capsys.readouterr().out


def test_rolling_window_option(example):
    result = example.run(
        use_synthetic=True, train_months=12, test_months=3, n_folds=4, window="rolling"
    )
    assert result["config"]["window"] == "rolling"
