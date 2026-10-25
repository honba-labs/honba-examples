"""strategies/13_bracket_order_mis: bracket order and MIS auto-square-off unit tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "examples" / "strategies" / "13_bracket_order_mis.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("bracket_order_mis", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_returns_expected_structure(example):
    result = example.run(bars=13)
    assert set(result) == {"strategy", "config", "result"}
    assert result["strategy"]["name"] == "bracket_order_mis"
    assert result["config"]["quantity"] == 10.0
    assert result["config"]["margin_multiplier"] == 5.0


def test_mis_auto_square_off_at_1520(example):
    """If neither TP nor SL is touched, the 15:20 bar forces a market square-off."""
    # Flat price path: neither 4% TP nor 2% SL is hit
    flat_path = [2500.0] * 13
    result = example.run(bars=13, price_path=flat_path)
    res = result["result"]
    fills = res["fills"]
    # Expect 1 buy entry at morning open and 1 sell exit at 15:20 IST
    assert len(fills) == 2
    assert fills[0]["side"] == "buy"
    assert fills[1]["side"] == "sell"
    assert fills[0]["quantity"] == fills[1]["quantity"]


def test_take_profit_bracket_trigger(example):
    """When price rises beyond take_profit_pct, take-profit exit fires before 15:20."""
    # Price jumps by 5% at bar index 3 (well before 15:20 at index 11)
    jump_path = [2500.0, 2500.0, 2500.0, 2650.0] + [2650.0] * 9
    result = example.run(bars=13, take_profit_pct=0.04, price_path=jump_path)
    fills = result["result"]["fills"]
    assert len(fills) == 2
    assert fills[0]["side"] == "buy"
    assert fills[1]["side"] == "sell"
    # Sell should occur at or above TP price
    assert fills[1]["price"] >= 2600.0


def test_stop_loss_bracket_trigger(example):
    """When price drops beyond stop_loss_pct, stop-loss exit fires before 15:20."""
    # Price drops by 3% at bar index 2 (SL is 2%)
    drop_path = [2500.0, 2500.0, 2420.0] + [2420.0] * 10
    result = example.run(bars=13, stop_loss_pct=0.02, price_path=drop_path)
    fills = result["result"]["fills"]
    assert len(fills) == 2
    assert fills[0]["side"] == "buy"
    assert fills[1]["side"] == "sell"
    assert fills[1]["price"] <= 2450.0


def test_main_happy_path_returns_zero(example, tmp_path, capsys):
    out = tmp_path / "bracket_mis.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    assert file_json["strategy"]["name"] == "bracket_order_mis"
    assert "bracket_order_mis" in stdout
