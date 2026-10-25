"""strategies/12_buy_and_hold_lifecycle: buy and hold lifecycle test."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = (
    Path(__file__).resolve().parents[2] / "examples" / "strategies" / "12_buy_and_hold_lifecycle.py"
)


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("buy_and_hold_lifecycle", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_returns_strategy_config_and_result(example):
    result = example.run(bars=20)
    assert result["strategy"]["name"] == "buy_and_hold_lifecycle"
    assert result["config"]["quantity"] == 1.0


def test_fills_only_once(example):
    result = example.run(bars=50)
    fills = result["result"]["fills"]
    assert len(fills) == 1
    assert fills[0]["side"] == "buy"
    assert fills[0]["quantity"] == 1.0


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main([]) == 0
    captured = capsys.readouterr().out
    assert "Strategy starting" in captured
    assert "Strategy stopping" in captured
