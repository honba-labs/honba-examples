"""strategies/07_declarative_long_short: Jesse-style SMA crossover with stop and target."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "strategies" / "07_declarative_long_short.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("declarative_long_short", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _intents(report):
    return report["result"]["intents"]


def test_run_returns_strategy_config_entries_and_result(example):
    report = example.run()
    assert set(report) == {"strategy", "config", "entries", "result"}
    assert report["strategy"]["name"] == "declarative_long_short"
    assert {"intents", "fills"} <= set(report["result"])
    json.dumps(report)


def test_long_entry_is_buy_with_protective_sell_orders_below_and_above(example):
    report = example.run()
    long_entry = next(e for e in report["entries"] if e["side"] == "buy")
    intents = _intents(report)
    i = next(
        k for k, it in enumerate(intents) if it["side"] == "buy" and it["order_type"] == "market"
    )
    entry = intents[i]
    assert entry["quantity"] == long_entry["quantity"]
    stop, take = intents[i + 1], intents[i + 2]
    assert (stop["side"], stop["order_type"]) == ("sell", "stop_market")
    assert (take["side"], take["order_type"]) == ("sell", "limit")
    assert stop["trigger_price"] == pytest.approx(long_entry["stop_loss"])
    assert take["price"] == pytest.approx(long_entry["take_profit"])
    assert stop["trigger_price"] < long_entry["price"] < take["price"]
    assert stop["quantity"] == take["quantity"] == entry["quantity"]


def test_short_entry_is_sell_with_protective_buy_orders_above_and_below(example):
    report = example.run()
    short_entry = next(e for e in report["entries"] if e["side"] == "sell")
    intents = _intents(report)
    i = next(
        k for k, it in enumerate(intents) if it["side"] == "sell" and it["order_type"] == "market"
    )
    stop, take = intents[i + 1], intents[i + 2]
    assert (stop["side"], stop["order_type"]) == ("buy", "stop_market")
    assert (take["side"], take["order_type"]) == ("buy", "limit")
    assert take["price"] < short_entry["price"] < stop["trigger_price"]
    assert stop["quantity"] == take["quantity"] == intents[i]["quantity"]


def test_long_comes_before_short(example):
    sides = [e["side"] for e in example.run()["entries"]]
    assert sides[0] == "buy" and "sell" in sides


def test_deterministic_across_two_runs(example):
    first = json.dumps(example.run(), sort_keys=True, default=str)
    second = json.dumps(example.run(), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "long_short.json"
    assert example.main(["--out", str(out)]) == 0
    stdout_json = json.loads(capsys.readouterr().out)
    assert json.loads(out.read_text()) == stdout_json
    assert stdout_json["strategy"]["name"] == "declarative_long_short"


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main([]) == 0
    assert "declarative_long_short" in capsys.readouterr().out
