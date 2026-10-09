"""backtesting/02_cost_modeling: India equity delivery cost breakdown."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "backtesting" / "02_cost_modeling.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("cost_modeling", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_returns_buy_and_sell_legs(example):
    result = example.run(price=2500.0, qty=10.0)
    assert set(result) == {"price", "quantity", "notional", "buy", "sell"}
    assert result["notional"] == 25000.0
    assert "legs" in result["buy"]
    assert "total_minor" in result["buy"]
    assert "total_rupees" in result["buy"]


def test_buy_and_sell_costs_differ_because_of_stt(example):
    result = example.run()
    # STT is only on sell side for delivery
    assert result["sell"]["total_minor"] > result["buy"]["total_minor"]


def test_deterministic_across_two_runs(example):
    import json as _json
    first = _json.dumps(example.run(), sort_keys=True, default=str)
    second = _json.dumps(example.run(), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "cost_modeling.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    stdout_json = json.loads(stdout)
    assert file_json == stdout_json
    assert "buy" in file_json


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main([]) == 0
    assert "legs" in capsys.readouterr().out