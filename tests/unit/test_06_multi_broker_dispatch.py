"""tests/unit/test_06_multi_broker_dispatch.py: production/06_multi_broker_dispatch parity."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = (
    Path(__file__).resolve().parents[2] / "examples" / "production" / "06_multi_broker_dispatch.py"
)


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("multi_broker_dispatch", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_shape(example):
    report = example.run(brokers="zerodha,angelone,upstox", bars=40, capital=50_000.0)
    assert set(report) >= {
        "strategy",
        "config",
        "brokers",
        "results",
        "parity_passed",
        "available_brokers",
        "unavailable_brokers",
    }
    assert report["strategy"] == "sma_crossover"
    assert report["config"]["brokers"] == ["zerodha", "angelone", "upstox"]
    assert len(report["brokers"]) == 3
    assert len(report["results"]) == 3
    assert isinstance(report["parity_passed"], bool)


def test_zerodha_and_angelone_available_with_capabilities(example):
    report = example.run(brokers="zerodha,angelone", bars=40)
    by_name = {r["broker"]: r for r in report["brokers"]}
    for broker in ("zerodha", "angelone"):
        entry = by_name[broker]
        assert entry["available"] is True, broker
        assert entry["reason"] == "", broker
        assert isinstance(entry["capabilities"], list) and len(entry["capabilities"]) > 0
        assert "PLACE_ORDER" in entry["capabilities"], broker


def test_upstox_reported_as_unavailable_scaffold(example):
    report = example.run(brokers="upstox", bars=40)
    entry = report["brokers"][0]
    assert entry["available"] is False
    assert "upstox" in report["unavailable_brokers"]
    assert "scaffold" in entry["reason"]


def test_parity_holds_across_brokers(example):
    report = example.run(brokers="zerodha,angelone", bars=60, capital=100_000.0)
    assert report["parity_passed"] is True
    equity = {
        r["final_equity"] for r in report["results"] if r["broker"] in ("zerodha", "angelone")
    }
    assert len(equity) == 1
    fill_keys = {
        tuple(
            sorted(
                (f["symbol"], f["side"], str(f["quantity"]), str(f["price"])) for f in r["fills"]
            )
        )
        for r in report["results"]
        if r["broker"] in ("zerodha", "angelone")
    }
    assert len(fill_keys) == 1


def test_deterministic_across_two_runs(example):
    a = json.dumps(
        example.run(brokers="zerodha,angelone,upstox", bars=50), sort_keys=True, default=str
    )
    b = json.dumps(
        example.run(brokers="zerodha,angelone,upstox", bars=50), sort_keys=True, default=str
    )
    assert a == b


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main([]) == 0
    out = capsys.readouterr().out
    assert "parity_passed" in out
    assert "zerodha" in out


def test_main_writes_json_to_out(example, tmp_path):
    out = tmp_path / "multi_broker.json"
    assert example.main(["--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["parity_passed"] is True
    assert "upstox" in report["unavailable_brokers"]
