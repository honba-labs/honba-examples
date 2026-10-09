"""strategies/06_equal_weight_rebalance: PortfolioStrategy composed from universe, weighting, schedule."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "strategies" / "06_equal_weight_rebalance.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("equal_weight_rebalance_example", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_returns_documented_json_shape(example):
    report = example.run(bars=20)
    assert set(report) == {"strategy", "config", "summary", "holdings", "result"}
    assert report["strategy"]["name"] == "equal_weight_rebalance"
    assert report["config"]["bars"] == 20
    summary = report["summary"]
    assert {"universe_size", "rebalances", "n_fills", "max_weight_deviation"} <= set(summary)
    assert summary["universe_size"] == 30
    assert len(report["holdings"]) == 30
    assert {"symbol", "quantity", "close", "weight"} <= set(report["holdings"][0])
    assert {"fills", "intents"} <= set(report["result"])
    json.dumps(report)  # fully serialisable


def test_first_rebalance_is_equal_weight_within_one_share(example):
    report = example.run(bars=1)  # a single session: only the initial rebalance happens
    assert report["summary"]["rebalances"] == 1
    target = report["config"]["capital"] * report["config"]["allocation"] / 30
    for holding in report["holdings"]:
        assert holding["quantity"] > 0
        assert abs(holding["quantity"] * holding["close"] - target) <= holding["close"]


def test_rebalances_every_n_trading_days(example):
    report = example.run(bars=40, rebalance_days=10)
    # day 0, then every 10 trading days: days 10, 20, 30
    assert report["summary"]["rebalances"] == 4


def test_deterministic_across_two_runs(example):
    first = json.dumps(example.run(bars=30), sort_keys=True, default=str)
    second = json.dumps(example.run(bars=30), sort_keys=True, default=str)
    assert first == second


def test_strategy_is_a_composed_portfolio_strategy(example):
    # Was: the TargetWeightStrategy subclass declared only ``universe``. Now no subclass exists;
    # the strategy is PortfolioStrategy composed from parts.
    from honba.strategies.portfolio import (
        EqualWeight,
        EveryNDays,
        NamedUniverse,
        PortfolioStrategy,
    )

    strat = example.build_strategy(rebalance_days=7, allocation=0.9)
    assert type(strat) is PortfolioStrategy
    assert strat.name == "equal_weight_rebalance"
    assert isinstance(strat.universe_source, NamedUniverse)
    assert isinstance(strat.weighting, EqualWeight)
    assert isinstance(strat.schedule, EveryNDays)
    assert strat.allocation == 0.9


def test_inverse_vol_gives_different_valid_result(example):
    base = example.run(bars=40)
    alt = example.run(bars=40, weighting="inverse_vol")
    assert alt["strategy"]["name"] != "equal_weight_rebalance"
    assert alt["config"]["weighting"] == "inverse_vol"
    assert alt["summary"]["universe_size"] == 30
    assert alt["summary"]["n_fills"] > 0
    assert 0 < alt["summary"]["final_cash"] < alt["config"]["capital"]
    assert all(h["quantity"] >= 0 for h in alt["holdings"])
    assert [h["quantity"] for h in alt["holdings"]] != [h["quantity"] for h in base["holdings"]]
    assert json.dumps(alt, sort_keys=True, default=str) == json.dumps(
        example.run(bars=40, weighting="inverse_vol"), sort_keys=True, default=str
    )


def test_monthly_schedule_rebalances_on_month_starts(example):
    base = example.run(bars=60)
    alt = example.run(bars=60, schedule="monthly:first_session")
    assert alt["config"]["schedule"] == "monthly:first_session"
    assert alt["summary"]["rebalances"] != base["summary"]["rebalances"]
    assert alt["summary"]["rebalances"] >= 2
    sessions = sorted({f["ts"] for f in alt["result"]["fills"]})
    assert len(sessions) == alt["summary"]["rebalances"]
    assert json.dumps(alt, sort_keys=True, default=str) == json.dumps(
        example.run(bars=60, schedule="monthly:first_session"), sort_keys=True, default=str
    )


def test_invalid_variant_is_rejected(example):
    with pytest.raises(ValueError):
        example.run(bars=5, weighting="bogus")


def test_main_accepts_weighting_and_schedule_flags(example, capsys):
    assert (
        example.main(
            ["--bars", "30", "--weighting", "inverse_vol", "--schedule", "monthly:first_session"]
        )
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["config"]["weighting"] == "inverse_vol"
    assert out["config"]["schedule"] == "monthly:first_session"


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "alpha30.json"
    assert example.main(["--bars", "20", "--out", str(out)]) == 0
    stdout_json = json.loads(capsys.readouterr().out)
    assert json.loads(out.read_text()) == stdout_json
    assert stdout_json["strategy"]["name"] == "equal_weight_rebalance"


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main(["--bars", "5"]) == 0
    assert "equal_weight_rebalance" in capsys.readouterr().out
