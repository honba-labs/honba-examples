"""backtesting/03_latency_modeling: compare fill models on synthetic data."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "backtesting" / "03_latency_modeling.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("latency_modeling", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_returns_three_models(example):
    result = example.run(bars=20)
    assert set(result) == {"config", "models"}
    assert set(result["models"]) == {"bar_close", "next_open", "next_close"}


def test_each_model_has_fill_counts(example):
    result = example.run(bars=60)
    for name, model in result["models"].items():
        assert "fills" in model
        assert "first_fill_price" in model


def test_deterministic_across_two_runs(example):
    import json as _json
    first = _json.dumps(example.run(bars=50), sort_keys=True, default=str)
    second = _json.dumps(example.run(bars=50), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "latency.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    stdout_json = json.loads(stdout)
    assert file_json == stdout_json


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main([]) == 0
    assert "bar_close" in capsys.readouterr().out