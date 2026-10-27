"""tests/unit/test_08_iron_condor_hedging: delta-hedged iron condor."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from honba_examples.options import GreekRiskBounds, check_greek_bounds

_PATH = Path(__file__).resolve().parents[2] / "examples" / "options" / "08_iron_condor_hedging.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("iron_condor_hedging", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_greek_bounds_checker() -> None:
    assert check_greek_bounds(0.0, 0.0, 0.0) == []
    breaches = check_greek_bounds(0.5, 0.0, 0.0)
    assert any("net_delta" in b for b in breaches)
    breaches = check_greek_bounds(0.0, 0.01, 0.0)
    assert any("net_gamma" in b for b in breaches)
    breaches = check_greek_bounds(0.0, 0.0, 1.0)
    assert any("net_vega" in b for b in breaches)
    custom = GreekRiskBounds(max_net_delta=0.05, max_gamma=0.0, max_vega=0.0)
    assert check_greek_bounds(0.1, 0.0, 0.0, custom)  # 0.1 > 0.05 breaches


def test_run_shape_and_credit(example) -> None:
    result = example.run(spot_at_entry=24000.0, iv=0.15, lot_size=50, dte=7, seed=42)
    assert set(result) == {
        "config",
        "entry",
        "greeks",
        "risk",
        "exit",
        "hedge",
        "summary",
    }
    assert result["entry"]["net_credit"] > 0
    assert result["entry"]["stt_total"] > 0
    assert result["config"]["dte"] == 7
    assert result["summary"]["net_pnl"] == pytest.approx(
        result["summary"]["close_pnl"]
        - result["summary"]["stt_total"]
        + result["summary"]["hedge_cash"]
    )


def test_greek_snapshots_span_steps(example) -> None:
    result = example.run(spot_at_entry=24000.0, iv=0.15, steps=72, seed=42)
    snapshots = result["greeks"]
    assert 1 <= len(snapshots) <= 72
    assert snapshots[0]["time"] == "09:20"
    expected_keys = {"step", "time", "spot", "tau_days", "net_delta", "net_gamma", "net_vega"}
    assert all(set(s) == expected_keys for s in snapshots)
    # tau decays through the session.
    assert snapshots[-1]["tau_days"] < snapshots[0]["tau_days"]


def test_delta_hedge_sign_offsets_delta(example) -> None:
    # Tight delta bound forces hedges; each hedge qty should move delta toward zero.
    result = example.run(
        spot_at_entry=24000.0,
        iv=0.15,
        delta_bound=0.02,
        gamma_bound=0.0,
        vega_bound=0.0,
        seed=42,
    )
    trades = result["hedge"]["trades"]
    if trades:
        first = trades[0]
        assert first["hedge_qty"] * first["net_delta_before"] < 0
        # Final delta should be no larger than the last pre-hedge delta magnitude.
        assert abs(result["risk"]["final_net_delta"]) <= abs(trades[-1]["net_delta_before"]) + 1.0


def test_deterministic_across_two_runs(example) -> None:
    first = json.dumps(example.run(seed=3), sort_keys=True, default=str)
    second = json.dumps(example.run(seed=3), sort_keys=True, default=str)
    # keep both lines identical; the point is determinism of the default config
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys) -> None:
    out = tmp_path / "hedge.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    assert json.loads(out.read_text()) == json.loads(stdout)
    assert json.loads(stdout)["entry"]["net_credit"] > 0


def test_main_happy_path_returns_zero(example) -> None:
    assert example.main([]) == 0
