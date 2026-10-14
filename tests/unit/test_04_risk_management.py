"""strategies/04_risk_management: risk guards on synthetic bars."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "strategies" / "04_risk_management.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("risk_management", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_returns_strategy_config_and_result(example):
    result = example.run(bars=20)
    assert set(result) == {"strategy", "config", "result"}
    assert result["strategy"]["name"] == "risk_managed_sma"
    assert "max_drawdown_pct" in result["config"]
    assert "max_daily_loss_pct" in result["config"]


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
    out = tmp_path / "risk_management.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    stdout_json = json.loads(stdout)
    assert file_json == stdout_json
    assert file_json["strategy"]["name"] == "risk_managed_sma"


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main([]) == 0
    assert "risk_managed_sma" in capsys.readouterr().out


def _bars(closes):
    import datetime as dt

    from honba_examples.synthetic import bar, weekdays

    days = weekdays(dt.date(2026, 1, 1), len(closes))
    prev = closes[0]
    out = []
    for d, c in zip(days, closes, strict=True):
        out.append(bar("RELIANCE", d, prev, c))
        prev = c
    return out


def _replay(example, closes, **kw):
    from honba.strategies.testing import replay

    strat = example.RiskManagedSma(**kw)
    return strat, replay(strat, _bars(closes))


def test_equity_estimate_subtracts_cash_spent_on_buys(example):
    closes = [100.0 + 0.5 * i for i in range(40)]
    strat, res = _replay(example, closes)
    assert len(res.fills) == 1 and res.fills[0].side.value == "buy"
    fill = res.fills[0]
    # starting cash + the position's unrealised P&L, not starting cash + the whole position value
    expected = 100_000.0 + fill.quantity * (closes[-1] - fill.price)
    assert strat._estimate_equity(closes[-1]) == pytest.approx(expected)


def test_exit_signal_is_not_blocked_by_the_position_cap(example):
    up = [100.0 + 1.25 * i for i in range(40)]
    down = [up[-1] - 0.5 * i for i in range(1, 60)]
    closes = up + down
    _, res = _replay(
        example, closes, max_drawdown_pct=0.9, max_daily_loss_pct=0.5, max_position_pct=0.2
    )
    sells = [f for f in res.fills if f.side.value == "sell"]
    assert sells, "the death cross must close the position even when it is over the cap"
    sma = lambda i, n: sum(closes[i - n + 1 : i + 1]) / n
    first_cross = next(i for i in range(40, len(closes)) if sma(i, 10) < sma(i, 30))
    sell_bar = next(i for i, b in enumerate(_bars(closes)) if b.ts == sells[0].ts)
    assert sell_bar == first_cross


def test_daily_loss_limit_flattens_on_a_gap_down(example):
    closes = [100.0 + 0.5 * i for i in range(40)] + [100.0]  # -17% gap on the last bar
    _, res = _replay(
        example, closes, max_drawdown_pct=0.9, max_daily_loss_pct=0.02, max_position_pct=0.2
    )
    assert [f.side.value for f in res.fills][-1] == "sell"
