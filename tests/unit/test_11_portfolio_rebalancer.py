"""backtesting/11_portfolio_rebalancer: a real periodic rebalance over a tiny synthetic catalog."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

from honba_examples.pipeline.base import Step

_PATH = (
    Path(__file__).resolve().parents[2] / "examples" / "backtesting" / "11_portfolio_rebalancer.py"
)
END = dt.date(2024, 6, 28)
ASOF = "2024-03-04"  # a Monday; the first forward session is Tuesday 2024-03-05


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("portfolio_rebalancer", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def data_root(catalog_dir, add_series):
    n = 120
    add_series("AAA", [100.0 + 2.0 * i for i in range(n)], last_date=END)  # strong riser
    add_series("BBB", [200.0 + 0.1 * i for i in range(n)], last_date=END)  # flat
    add_series("CCC", [300.0 - 0.8 * i for i in range(n)], last_date=END)  # faller
    return catalog_dir.parents[2]  # <root>/catalog/1D/NSE -> <root>


def _run_json(example, capsys, *args, data_root):
    argv = ["--format", "json", "--data-dir", str(data_root), *args]
    assert example.main(argv) == 0
    return json.loads(capsys.readouterr().out)


def _args(**extra):
    base = ["--symbols", "AAA,BBB,CCC", "--asof", ASOF, "--end-date", str(END)]
    base += ["--capital", "300000", "--rebalance-days", "10"]
    for key, value in extra.items():
        base += [f"--{key.replace('_', '-')}", str(value)]
    return base


def test_it_really_rebalances_back_toward_equal_weight(example, capsys, data_root):
    out = _run_json(example, capsys, *_args(), data_root=data_root)
    assert out["mode"] == "rebalance" and out["rebalanced"] is True
    summary = out["summary"]
    assert summary["n_rebalances"] >= 5
    assert summary["total_fees"] > 0
    assert summary["settlement_days"] in (1, 2)
    weights = [s["weight_pct"] for s in out["stocks"]]
    assert len(weights) == 3
    assert (
        max(abs(w - 100 / 3) for w in weights) < 6.0
    )  # a 2.0/bar riser would be ~60% unrebalanced
    assert out["rebalances"]  # trades per rebalance


def test_day_zero_is_the_session_after_asof(example, capsys, data_root):
    out = _run_json(example, capsys, *_args(), data_root=data_root)
    assert out["summary"]["start_date"] == "2024-03-05"


def test_sip_amount_is_added_at_each_rebalance(example, capsys, data_root):
    plain = _run_json(example, capsys, *_args(), data_root=data_root)
    sip = _run_json(example, capsys, *_args(sip_amount=5000), data_root=data_root)
    assert sip["summary"]["total_invested"] > plain["summary"]["total_invested"]
    assert sip["summary"]["sip_per_rebalance"] == 5000.0


def test_missing_symbols_are_reported_not_hidden(example, capsys, data_root):
    args = ["--symbols", "AAA,BBB,ZZZ", "--asof", ASOF, "--end-date", str(END)]
    out = _run_json(example, capsys, *args, data_root=data_root)
    assert out["summary"]["missing_data"] == ["ZZZ"]
    assert any("ZZZ" in n for n in out["notes"])


def test_allocation_score_is_a_weighted_buy_and_hold(example, capsys, data_root, monkeypatch):
    def fake_screen(self, asof):
        return ["AAA", "BBB"], [
            {"symbol": "AAA", "mb_score": 30.0},
            {"symbol": "BBB", "mb_score": 10.0},
        ]

    monkeypatch.setattr(example.PortfolioRebalancerExample, "_screen", fake_screen)
    args = ["--asof", ASOF, "--end-date", str(END), "--capital", "100000", "--allocation", "score"]
    out = _run_json(example, capsys, *args, data_root=data_root)
    assert out["mode"] == "buy_and_hold" and out["rebalanced"] is False
    assert out["weights"] == pytest.approx({"AAA": 0.75, "BBB": 0.25}, abs=0.001)
    assert any("nothing is rebalanced" in n for n in out["notes"])


def test_allocation_without_screener_fields_falls_back_to_equal_rebalance(
    example, capsys, data_root
):
    out = _run_json(example, capsys, *_args(allocation="score"), data_root=data_root)
    assert out["mode"] == "rebalance" and out["allocation"] == "equal"


def test_strategy_all_unions_all_three_screeners(example, capsys, data_root, monkeypatch):
    from honba_examples.pipeline import screeners

    calls: list[str] = []

    def fake(name, picks):
        class Fake(Step):
            def __init__(self, asof=None, top=5):
                self.asof, self.top = asof, top

            def run(self, ctx):
                calls.append(name)
                ctx["symbols"] = list(picks)
                ctx["candidates"] = [{"symbol": s} for s in picks]
                return ctx

        return Fake

    monkeypatch.setattr(screeners, "MultibaggerScreener", fake("multibagger", ["AAA", "BBB"]))
    monkeypatch.setattr(screeners, "ValueScreener", fake("value", ["BBB", "CCC"]))
    monkeypatch.setattr(screeners, "DividendYieldScreener", fake("dividend", ["CCC", "AAA"]))
    args = ["--strategy", "all", "--asof", ASOF, "--end-date", str(END), "--capital", "300000"]
    out = _run_json(example, capsys, *args, data_root=data_root)
    assert calls == ["multibagger", "value", "dividend"]
    assert out["symbols"] == ["AAA", "BBB", "CCC"]


def test_union_screens_keeps_first_seen_order_and_candidate_rows(example):
    symbols, cands = example.union_screens(
        [(["B", "A"], [{"symbol": "B", "x": 1}]), (["A", "C"], [{"symbol": "C", "x": 3}])]
    )
    assert symbols == ["B", "A", "C"]
    assert [c["symbol"] for c in cands] == ["B", "A", "C"]
    assert cands[0]["x"] == 1 and cands[2]["x"] == 3
