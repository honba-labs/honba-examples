"""tests/unit/test_06_nifty_920_straddle: 09:20 short straddle with per-leg stops."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from honba_examples.options import option_sell_stt

_PATH = Path(__file__).resolve().parents[2] / "examples" / "options" / "06_nifty_920_straddle.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("nifty_920_straddle", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_shape_and_stt(example) -> None:
    result = example.run(spot_at_entry=22000.0, iv=0.15, lot_size=50, steps=72, seed=42)
    assert set(result) == {
        "config",
        "entry",
        "costs",
        "exits",
        "summary",
    }
    assert result["config"]["strike"] == 22000
    assert result["entry"]["total_credit"] > 0
    call_prem = result["entry"]["call_premium"]
    put_prem = result["entry"]["put_premium"]
    expected_stt = float(option_sell_stt(call_prem, 50) + option_sell_stt(put_prem, 50))
    assert result["costs"]["stt_total"] == pytest.approx(expected_stt)
    assert result["costs"]["stt_call"] + result["costs"]["stt_put"] == pytest.approx(
        result["costs"]["stt_total"]
    )
    assert result["summary"]["net_pnl"] == pytest.approx(
        result["summary"]["gross_pnl"] - result["costs"]["stt_total"]
    )


def test_exit_logic_stop_and_squareoff(example) -> None:
    result = example.run(spot_at_entry=22000.0, iv=0.15, lot_size=50, seed=42)
    exits = result["exits"]
    # One leg is stopped, the other is squared off at 15:20 (last step).
    stopped = [
        leg
        for leg in ("call", "put")
        if exits[leg]["exit_step"] is not None and exits[leg]["exit_step"] < 71
    ]
    assert len(stopped) >= 1
    # Every exit premium is >= 0 and every leg P&L = (entry - exit) * lot.
    for leg in ("call", "put"):
        entry = result["entry"][leg + "_premium"]
        assert exits[leg]["exit_premium"] >= 0
        assert exits[leg]["pnl"] == pytest.approx((entry - exits[leg]["exit_premium"]) * 50)


def test_deterministic_across_two_runs(example) -> None:
    first = json.dumps(example.run(spot_at_entry=22000.0, seed=7), sort_keys=True, default=str)
    second = json.dumps(example.run(spot_at_entry=22000.0, seed=7), sort_keys=True, default=str)
    assert first == second


def test_different_seed_changes_path(example) -> None:
    a = example.run(spot_at_entry=22000.0, seed=1)["summary"]["max_adverse_spot_move_pct"]
    b = example.run(spot_at_entry=22000.0, seed=2)["summary"]["max_adverse_spot_move_pct"]
    assert a != b


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys) -> None:
    out = tmp_path / "straddle.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    stdout_json = json.loads(stdout)
    assert file_json == stdout_json
    assert file_json["config"]["underlying"] == "NIFTY"


def test_main_happy_path_returns_zero(example, capsys) -> None:
    assert example.main([]) == 0
    assert '"NIFTY"' in capsys.readouterr().out
