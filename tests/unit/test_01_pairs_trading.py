"""tests/unit/test_01_pairs_trading: statistical pairs trading."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "examples" / "stocksharp" / "01_pairs_trading.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("pairs_trading", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_keys_and_estimation(example) -> None:
    result = example.run(bars=252, seed=42)
    assert set(result) == {
        "config",
        "estimation",
        "spread",
        "trading",
    }
    assert result["config"]["symbol_a"] == "ICICIBANK"
    assert result["config"]["symbol_b"] == "HDFCBANK"
    est = result["estimation"]
    assert est["cointegrated"] is True  # synthetic residual is engineered mean-reverting
    assert 0.5 < est["beta"] < 1.5
    assert est["dickey_fuller_t"] < est["dickey_fuller_5pct"]
    assert est["half_life_days"] > 0
    assert est["half_life_days"] != float("inf")


def test_trading_structure(example) -> None:
    result = example.run(bars=252, seed=42)
    trading = result["trading"]
    for trade in trading["trades"]:
        assert set(trade) >= {"bar", "side", "z", "spread"}
        assert trade["side"] in ("long_spread", "short_spread", "exit")
    assert trading["total_pnl"] == trading["realised_pnl"] + trading["unrealised_pnl"]
    assert trading["final_position"] in (-1.0, 0.0, 1.0)


def test_deterministic_across_two_runs(example) -> None:
    first = json.dumps(example.run(bars=120, seed=9), sort_keys=True, default=str)
    second = json.dumps(example.run(bars=120, seed=9), sort_keys=True, default=str)
    assert first == second


def test_entry_exit_z_respected(example) -> None:
    result = example.run(bars=252, seed=42, entry_z=3.0, exit_z=0.5)
    for trade in result["trading"]["trades"]:
        if trade["side"] == "exit":
            assert abs(trade["z"]) <= 0.5 + 1e-9


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys) -> None:
    out = tmp_path / "pairs.json"
    assert example.main(["--out", str(out)]) == 0
    assert json.loads(out.read_text()) == json.loads(capsys.readouterr().out)


def test_main_happy_path_returns_zero(example) -> None:
    assert example.main([]) == 0
