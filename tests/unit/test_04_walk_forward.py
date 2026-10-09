"""backtesting/04_walk_forward: walk-forward analysis on synthetic data."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "backtesting" / "04_walk_forward.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("walk_forward", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_returns_windows(example):
    result = example.run(bars=120, window=40, step=20)
    assert "config" in result
    assert "windows" in result
    assert len(result["windows"]) > 0
    for w in result["windows"]:
        assert set(w) == {"window", "train_range", "test_range", "best_fast", "best_slow", "train_score", "test_score"}


def test_deterministic_across_two_runs(example):
    import json as _json
    first = _json.dumps(example.run(), sort_keys=True, default=str)
    second = _json.dumps(example.run(), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "walk_forward.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    stdout_json = json.loads(stdout)
    assert file_json == stdout_json


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main([]) == 0
    assert "windows" in capsys.readouterr().out