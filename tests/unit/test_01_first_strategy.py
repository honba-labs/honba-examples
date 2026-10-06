"""strategies/01_first_strategy: SMA crossover on synthetic bars with replay."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "strategies" / "01_first_strategy.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("first_strategy", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_returns_strategy_config_and_result(example):
    result = example.run(bars=20)
    assert set(result) == {"strategy", "config", "result"}
    assert result["strategy"]["name"] == "sma_crossover"
    assert result["config"]["fast"] == 10
    assert result["config"]["slow"] == 30
    assert result["config"]["quantity"] == 1.0


def test_result_contains_fills_and_intents(example):
    result = example.run(bars=100)
    res = result["result"]
    # replay returns a ReplayResult dataclass; jsonable converts it
    assert "fills" in res
    assert "intents" in res


def test_fast_slow_crossover_produces_expected_signal_count(example):
    # Upward drift: 2500 + i*0.5 → fast SMA crosses above slow exactly once
    # (after ~30 bars warmup), then stays above. Downward would be symmetric.
    result = example.run(bars=60, fast=10, slow=30, quantity=1.0)
    res = result["result"]
    fills = res["fills"]
    assert len(fills) >= 1
    first_fill = fills[0]
    assert first_fill["side"] == "buy"
    assert first_fill["quantity"] == 1.0


def test_deterministic_across_two_runs(example):
    import json as _json
    first = _json.dumps(example.run(bars=50), sort_keys=True, default=str)
    second = _json.dumps(example.run(bars=50), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "first.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    stdout_json = json.loads(stdout)
    assert file_json == stdout_json
    assert file_json["strategy"]["name"] == "sma_crossover"


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main([]) == 0
    assert "sma_crossover" in capsys.readouterr().out