"""backtesting/05_monte_carlo: bootstrap equity curves on synthetic data."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "backtesting" / "05_monte_carlo.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("monte_carlo", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_returns_mc_results(example):
    result = example.run(paths=50)  # bars=60 now raises: too few trades
    assert "config" in result
    assert "mc" in result
    assert "final_equity" in result["mc"]
    assert "max_drawdown_pct" in result["mc"]
    assert "sharpe" in result["mc"]


def test_mc_results_have_percentiles(example):
    result = example.run(paths=50)
    mc = result["mc"]
    for key in ["final_equity", "max_drawdown_pct", "sharpe"]:
        assert "mean" in mc[key]
        assert "p5" in mc[key]
        assert "p95" in mc[key]


def test_deterministic_across_two_runs(example):
    import json as _json

    first = _json.dumps(example.run(paths=50), sort_keys=True, default=str)
    second = _json.dumps(example.run(paths=50), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "monte_carlo.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    stdout_json = json.loads(stdout)
    assert file_json == stdout_json


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main([]) == 0
    assert "mc" in capsys.readouterr().out


def _trade(side, price, ts):
    from honba.entities.instrument import InstrumentId
    from honba.entities.order import OrderSide
    from honba.entities.trade import Trade

    return Trade(
        InstrumentId("X", "NSE"), OrderSide.BUY if side == "b" else OrderSide.SELL, 1, price, ts
    )


def test_round_trips_pair_each_buy_with_its_sell_and_drop_the_open_leg(example):
    day = 86_400 * 10**9
    fills = [
        _trade("b", 100.0, day),
        _trade("s", 110.0, 2 * day),
        _trade("b", 120.0, 3 * day),
        _trade("s", 108.0, 4 * day),
        _trade("b", 90.0, 5 * day),  # still open at the end
    ]
    trips = example._round_trips(fills)
    assert [(t.entry_price, t.exit_price) for t in trips] == [(100.0, 110.0), (120.0, 108.0)]
    assert [t.ret for t in trips] == pytest.approx([0.10, -0.10])


def test_bootstrap_curve_compounds_trade_returns_in_drawn_order(example):
    curve = example._bootstrap_curve([0.10, -0.10], capital=1_000.0, dates=["d0", "d1", "d2"])
    assert [c["value"] for c in curve] == pytest.approx([1_000.0, 1_100.0, 990.0])
    assert [c["date"] for c in curve] == ["d0", "d1", "d2"]


def test_default_run_has_enough_trades_and_a_real_spread(example):
    result = example.run()
    assert result["base"]["round_trips"] >= result["config"]["min_trades"]
    mc = result["mc"]
    assert mc["final_equity"]["p5"] < mc["final_equity"]["p95"]
    assert mc["max_drawdown_pct"]["p5"] != mc["max_drawdown_pct"]["p95"]


def test_too_few_trades_is_a_clear_error_not_flat_percentiles(example):
    with pytest.raises(ValueError, match=r"round-trip trades.*at least"):
        example.run(bars=100)


def test_main_reports_too_few_trades_as_a_usage_error(example, capsys):
    with pytest.raises(SystemExit) as exc:
        example.main(["--bars", "100"])
    assert exc.value.code == 2
    assert "round-trip" in capsys.readouterr().err


def test_run_does_not_touch_the_global_random_state(example):
    import random

    random.seed(123)
    before = random.getstate()
    example.run(paths=20)
    assert random.getstate() == before
