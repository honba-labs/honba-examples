"""backtesting/01_first_backtest: SMA backtest on synthetic data with replay."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "backtesting" / "01_first_backtest.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("first_backtest", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_returns_strategy_config_and_result(example):
    result = example.run(bars=20)
    assert set(result) == {"strategy", "config", "result", "summary"}
    assert result["strategy"]["name"] == "sma_backtest"
    assert result["config"]["fast"] == 10
    assert result["config"]["slow"] == 30


def test_result_contains_fills_and_intents(example):
    result = example.run(bars=60)
    res = result["result"]
    assert "fills" in res
    assert "intents" in res


def test_deterministic_across_two_runs(example):
    import json as _json

    first = _json.dumps(example.run(bars=50), sort_keys=True, default=str)
    second = _json.dumps(example.run(bars=50), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "first_backtest.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    stdout_json = json.loads(stdout)
    assert file_json == stdout_json
    assert file_json["strategy"]["name"] == "sma_backtest"


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main([]) == 0
    assert "sma_backtest" in capsys.readouterr().out


@pytest.fixture
def synthetic_only(monkeypatch):
    import honba_examples.synthetic as syn

    monkeypatch.setattr(syn, "load_real_bars", lambda *a, **k: [])


def test_orders_fill_at_the_next_bar_open(example, synthetic_only):
    """The first signal is on the 30th bar (close 2514.5); the fill is the next open, 2515.0."""
    fills = example.run(bars=60)["result"]["fills"]
    assert fills[0]["side"] == "buy"
    assert fills[0]["price"] == pytest.approx(2515.0)


def test_capital_sizes_the_position_and_marks_the_equity(example, synthetic_only):
    small = example.run(bars=60, capital=10_000.0)
    big = example.run(bars=60, capital=100_000.0)
    price = big["result"]["fills"][0]["price"]
    assert big["result"]["fills"][0]["quantity"] == int(100_000.0 // price)
    assert small["result"]["fills"][0]["quantity"] == int(10_000.0 // price)
    assert big["summary"]["final_equity"] > 100_000.0  # the ramp is profitable, gross of costs
    assert big["summary"]["net_pnl"] == pytest.approx(big["summary"]["final_equity"] - 100_000.0)


def test_report_says_which_data_source_was_used(example, synthetic_only):
    result = example.run(bars=60)
    assert result["config"]["data_source"] == "synthetic"
    assert result["summary"]["data_source"] == "synthetic"


def test_synthetic_flag_ignores_real_bars(example, monkeypatch):
    import honba_examples.synthetic as syn

    monkeypatch.setattr(
        syn, "load_real_bars", lambda *a, **k: syn.trending_bars("RELIANCE", 60, base=50.0)
    )
    assert example.run(bars=60)["config"]["data_source"] == "lakehouse"
    assert example.run(bars=60, synthetic=True)["config"]["data_source"] == "synthetic"
