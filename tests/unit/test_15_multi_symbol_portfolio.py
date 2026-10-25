"""strategies/15_multi_symbol_portfolio: 5-scrip portfolio unit tests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = (
    Path(__file__).resolve().parents[2] / "examples" / "strategies" / "15_multi_symbol_portfolio.py"
)


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("multi_symbol_portfolio", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["multi_symbol_portfolio"] = module
    spec.loader.exec_module(module)
    return module


def test_run_returns_expected_structure(example):
    result = example.run(bars=50)
    assert set(result) == {"strategy", "config", "result"}
    assert result["strategy"]["name"] == "multi_symbol_portfolio"
    assert len(result["config"]["symbols"]) == 5
    assert result["config"]["max_weight"] == 0.20


def test_trades_across_all_five_symbols(example):
    result = example.run(bars=80)
    fills = result["result"]["fills"]
    symbols_traded = {f["instrument_id"]["symbol"] for f in fills}
    expected = {"RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"}
    assert symbols_traded == expected


def test_per_symbol_weight_cap_enforced(example):
    # Run with max_weight = 0.15 (15%)
    result = example.run(bars=60, max_weight=0.15)
    fills = result["result"]["fills"]
    for f in fills:
        if f["side"] == "buy":
            # Initial capital is 100k, 15% is 15,000 INR
            notional = f["quantity"] * f["price"]
            assert notional <= 16_000.0  # within margin of integer lot rounding


def test_main_happy_path_returns_zero(example, tmp_path, capsys):
    out = tmp_path / "multi_symbol.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    assert file_json["strategy"]["name"] == "multi_symbol_portfolio"
    assert "multi_symbol_portfolio" in stdout
