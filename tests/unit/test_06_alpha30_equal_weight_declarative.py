"""strategies/06_alpha30_equal_weight_declarative: declared universe, framework rebalances."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = (
    Path(__file__).resolve().parents[2] / "strategies" / "06_alpha30_equal_weight_declarative.py"
)


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("alpha30_declarative", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_returns_documented_json_shape(example):
    report = example.run(bars=20)
    assert set(report) == {"strategy", "config", "summary", "holdings", "result"}
    assert report["strategy"]["name"] == "alpha30_equal_weight_declarative"
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


def test_strategy_only_declares_the_universe(example):
    cls = example.Alpha30EqualWeightDeclarative
    own = {k for k, v in vars(cls).items() if callable(v) and not k.startswith("__")}
    assert own == {"universe"}


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "alpha30.json"
    assert example.main(["--bars", "20", "--out", str(out)]) == 0
    stdout_json = json.loads(capsys.readouterr().out)
    assert json.loads(out.read_text()) == stdout_json
    assert stdout_json["strategy"]["name"] == "alpha30_equal_weight_declarative"


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main(["--bars", "5"]) == 0
    assert "alpha30_equal_weight_declarative" in capsys.readouterr().out
