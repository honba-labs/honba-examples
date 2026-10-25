"""strategies/16_dynamic_position_sizing: dynamic position sizing unit tests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "strategies"
    / "16_dynamic_position_sizing.py"
)


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("dynamic_position_sizing", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["dynamic_position_sizing"] = module
    spec.loader.exec_module(module)
    return module


def test_run_returns_expected_structure(example):
    result = example.run(bars=50, mode="fixed_fractional")
    assert set(result) == {"strategy", "config", "result"}
    assert result["strategy"]["name"] == "dynamic_position_sizing"
    assert result["config"]["mode"] == "fixed_fractional"
    assert "fills" in result["result"]
    assert "sizes_history" in result["result"]


def test_fixed_lot_mode(example):
    result = example.run(bars=100, mode="fixed_lot", fixed_quantity=15.0)
    fills = result["result"]["fills"]
    buy_fills = [f for f in fills if f["side"] == "buy"]
    assert len(buy_fills) >= 1
    for f in buy_fills:
        assert f["quantity"] == 15.0


def test_fixed_fractional_mode(example):
    result = example.run(bars=100, mode="fixed_fractional", risk_pct=0.01)
    fills = result["result"]["fills"]
    buy_fills = [f for f in fills if f["side"] == "buy"]
    assert len(buy_fills) >= 1
    # Risk 1% of 100k = 1000 INR; 2% stop on ~2500 price = 50 INR stop -> ~20 shares
    assert 10 <= buy_fills[0]["quantity"] <= 30


def test_volatility_targeted_mode(example):
    result = example.run(bars=100, mode="volatility_targeted", risk_pct=0.01)
    fills = result["result"]["fills"]
    buy_fills = [f for f in fills if f["side"] == "buy"]
    assert len(buy_fills) >= 1
    assert buy_fills[0]["quantity"] >= 1


def test_compare_mode(example):
    result = example.run(bars=60, compare=True)
    assert result["strategy"]["name"] == "dynamic_position_sizing"
    res = result["result"]
    assert "fixed_lot" in res
    assert "fixed_fractional" in res
    assert "volatility_targeted" in res
    for mode_data in res.values():
        assert "avg_position_size" in mode_data
        assert "trade_count" in mode_data


def test_main_happy_path_returns_zero(example, tmp_path, capsys):
    out = tmp_path / "sizing.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    assert file_json["strategy"]["name"] == "dynamic_position_sizing"
    assert "dynamic_position_sizing" in stdout
