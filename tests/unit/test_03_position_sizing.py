"""strategies/03_position_sizing: three sizing methods on synthetic bars."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "strategies" / "03_position_sizing.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("position_sizing", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_returns_strategy_config_and_result(example):
    result = example.run(bars=20)
    assert set(result) == {"strategy", "config", "result"}
    assert result["strategy"]["name"] == "sma_sizing"
    assert result["config"]["sizing"] == "fixed"


def test_result_contains_fills_and_intents(example):
    result = example.run(bars=100)
    res = result["result"]
    assert "fills" in res
    assert "intents" in res


def test_deterministic_across_two_runs(example):
    import json as _json

    first = _json.dumps(example.run(bars=50), sort_keys=True, default=str)
    second = _json.dumps(example.run(bars=50), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "position_sizing.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    stdout_json = json.loads(stdout)
    assert file_json == stdout_json
    assert file_json["strategy"]["name"] == "sma_sizing"


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main([]) == 0
    assert "sma_sizing" in capsys.readouterr().out


@pytest.mark.parametrize("sizing", ["fixed", "volatility", "kelly"])
def test_every_sizing_mode_places_and_fills_orders(example, sizing):
    """The harness must see the intents: a silent no-op once looked like success."""
    res = example.run(sizing=sizing)["result"]
    assert len(res["intents"]) >= 2
    assert len(res["fills"]) == len(res["intents"])


def test_fractional_kelly_formula_and_cap(example):
    wins_loses = [0.02] * 6 + [-0.01] * 4  # p=0.6, b=2 -> full Kelly 0.4
    assert example.fractional_kelly(wins_loses, fraction=0.5, cap=0.25, min_trades=5) == (
        pytest.approx(0.2)
    )
    assert example.fractional_kelly(wins_loses, fraction=1.0, cap=0.25, min_trades=5) == 0.25


def test_fractional_kelly_no_edge_and_too_few_trades(example):
    assert example.fractional_kelly([-0.01, 0.01, -0.02, 0.005, -0.01], min_trades=5) == 0.0
    assert example.fractional_kelly([0.02, 0.03], min_trades=5) is None
    assert example.fractional_kelly([0.01] * 6, min_trades=5) == 0.25  # no losses: cap only


def test_kelly_mode_sizes_from_trailing_trades_and_is_capped(example):
    res = example.run(sizing="kelly")["result"]
    buys = [f["quantity"] for f in res["fills"] if f["side"] == "buy"]
    equity = 100_000.0
    assert len(buys) >= 5
    assert buys[0] == 1  # no trade history yet: the fixed_qty fallback
    assert max(buys) > 1  # later entries are sized from the trade stats
    for f in res["fills"]:
        if f["side"] == "buy":
            assert f["quantity"] * f["price"] <= equity * 0.25 + f["price"]


def test_volatility_sizing_never_exceeds_equity(example):
    res = example.run(sizing="volatility")["result"]
    for f in res["fills"]:
        if f["side"] == "buy":
            assert f["quantity"] * f["price"] <= 100_000.0
