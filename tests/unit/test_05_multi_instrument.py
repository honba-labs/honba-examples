"""strategies/05_multi_instrument: basket SMA on synthetic bars."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "strategies" / "05_multi_instrument.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("multi_instrument", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_returns_strategy_config_and_result(example):
    result = example.run(bars=20)
    assert set(result) == {"strategy", "config", "result"}
    assert result["strategy"]["name"] == "basket_sma"
    assert result["config"]["symbols"] == ["RELIANCE", "TCS", "INFY", "HDFCBANK"]


def test_result_contains_fills_and_intents(example):
    result = example.run(bars=100)
    res = result["result"]
    assert "fills" in res
    assert "intents" in res


def test_deterministic_across_two_runs(example):
    import json as _json

    first = _json.dumps(example.run(bars=50), sort_keys=True, default=str)
    second = _json.dumps(example.run(bars=50), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "multi_instrument.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    stdout_json = json.loads(stdout)
    assert file_json == stdout_json
    assert file_json["strategy"]["name"] == "basket_sma"


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main([]) == 0
    assert "basket_sma" in capsys.readouterr().out


def _replay(example, bars=100, **kw):
    from honba.strategies.testing import replay

    symbols = ("RELIANCE", "TCS", "INFY", "HDFCBANK")
    bars_list = example.basket_bars(bars, symbols)
    strat = example.BasketSma(**kw)
    res = replay(strat, bars_list)
    last = {b.instrument_id.symbol: b.close for b in bars_list}
    return strat, res, last


def _max_weight_drift(strat, last):
    values = {s: strat.position(strat._iids[s]) * last[s] for s in last}
    total = sum(values.values())
    return max(abs(v / total - 1 / len(values)) for v in values.values())


def test_rebalance_places_orders_toward_equal_weight(example):
    strat, res, last = _replay(example, bars=160, rebalance_every=20)
    _, res_off, _ = _replay(example, bars=160, rebalance_every=10_000)
    assert len(res.intents) > len(res_off.intents)  # the rebalance sent orders
    assert _max_weight_drift(strat, last) < 0.02


def test_without_rebalance_the_weights_drift(example):
    strat, _, last = _replay(example, bars=160, rebalance_every=10_000)
    assert _max_weight_drift(strat, last) > 0.02


def test_bar_count_is_per_symbol(example):
    strat, _, _ = _replay(example, bars=100)
    # 30-bar slow SMA: the first usable bar is the 30th, so 71 usable bars per symbol
    assert strat._bar_count == {"RELIANCE": 71, "TCS": 71, "INFY": 71, "HDFCBANK": 71}
