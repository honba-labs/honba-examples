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
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_returns_three_models(example):
    result = example.run(bars=20)
    assert set(result) == {"config", "models"}
    assert set(result["models"]) == {"bar_close", "next_open", "open_after_2_bars"}


def test_each_model_has_fill_counts(example):
    result = example.run(bars=60)
    for model in result["models"].values():
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


def test_fast_and_slow_reach_the_strategy(example):
    default = example.run(bars=60)["models"]["bar_close"]["first_fill_price"]
    short = example.run(bars=60, fast=5, slow=20)["models"]["bar_close"]["first_fill_price"]
    assert default == pytest.approx(2514.5)  # first usable bar is the 30th: 2500 + 29 * 0.5
    assert short == pytest.approx(2509.5)  # first usable bar is the 20th: 2500 + 19 * 0.5


def test_each_extra_bar_of_delay_moves_the_fill_one_step_on_the_ramp(example):
    models = example.run(bars=60)["models"]
    assert models["next_open"]["first_fill_price"] == pytest.approx(2515.0)
    assert models["open_after_2_bars"]["first_fill_price"] == pytest.approx(2515.5)
    assert models["open_after_2_bars"]["fill_delay"] == 2
