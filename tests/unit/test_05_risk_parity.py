"""tests/unit/test_05_risk_parity: inverse-volatility risk parity rebalancer."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "examples" / "stocksharp" / "05_risk_parity.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("risk_parity", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_risk_parity_weights_are_inverse_vol(example) -> None:
    result = example.run(symbols=10, bars=260, rebalance_days=20, seed=42)
    summary = result["summary"]
    assert summary["universe_size"] == 10
    assert summary["rebalances"] >= 1
    assert summary["positions_handled"] >= 1
    holdings = {h["symbol"]: h for h in result["holdings"]}
    # SCRIP01 has the lowest vol (0.5%/day), SCRIP10 the highest (4.1%/day),
    # so SCRIP01 should carry the most weight and SCRIP10 the least.
    assert holdings["SCRIP01"]["weight"] > holdings["SCRIP10"]["weight"]
    total_weight = sum(h["weight"] for h in result["holdings"])
    assert total_weight == pytest.approx(0.98, rel=0.25)  # allocation budget
    assert summary["max_weight_deviation"] >= 0.0


def test_rebalance_cadence(example) -> None:
    a = example.run(symbols=6, bars=260, rebalance_days=10, seed=42)
    b = example.run(symbols=6, bars=260, rebalance_days=50, seed=42)
    assert a["summary"]["rebalances"] >= b["summary"]["rebalances"]


def test_deterministic_across_two_runs(example) -> None:
    first = json.dumps(example.run(seed=13), sort_keys=True, default=str)
    second = json.dumps(example.run(seed=13), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys) -> None:
    out = tmp_path / "rp.json"
    assert example.main(["--out", str(out)]) == 0
    assert json.loads(out.read_text()) == json.loads(capsys.readouterr().out)


def test_main_happy_path_returns_zero(example) -> None:
    assert example.main([]) == 0
