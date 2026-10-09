"""backtesting/07_sip_buy_and_hold: unit tests for buy-and-hold SIP backtest."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

from tests.synthetic import bar as synth_bar
from tests.synthetic import weekdays as weekdays_func

_PATH = Path(__file__).resolve().parents[2] / "backtesting" / "07_sip_buy_and_hold.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("sip_buy_and_hold", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def synthetic_bars():
    start = dt.date(2025, 1, 1)
    dates = weekdays_func(start, 260)  # ~1 year of daily bars
    return [synth_bar("TESTETF", d, 100.0 + i * 0.1) for i, d in enumerate(dates)]


def test_run_monthly_sip(example, synthetic_bars):
    result = example.run(
        symbol="TESTETF",
        frequency="monthly",
        sip_amount=5000.0,
        initial_corpus=0.0,
        bars=synthetic_bars,
    )
    assert set(result) == {"config", "summary", "installments", "curve"}
    summary = result["summary"]
    assert summary["symbol"] == "TESTETF"
    assert summary["frequency"] == "monthly"
    assert summary["installments_count"] >= 10
    assert summary["total_invested"] > 0
    assert summary["total_units"] > 0
    assert summary["final_portfolio_value"] > 0


def test_run_weekly_sip(example, synthetic_bars):
    result = example.run(
        symbol="TESTETF",
        frequency="weekly",
        sip_amount=1000.0,
        initial_corpus=0.0,
        bars=synthetic_bars,
    )
    summary = result["summary"]
    assert summary["frequency"] == "weekly"
    assert summary["installments_count"] >= 45
    assert summary["total_invested"] == summary["installments_count"] * 1000.0
    assert summary["total_units"] > 0


def test_run_with_initial_corpus(example, synthetic_bars):
    result = example.run(
        symbol="TESTETF",
        frequency="monthly",
        sip_amount=2000.0,
        initial_corpus=10000.0,
        bars=synthetic_bars,
    )
    summary = result["summary"]
    assert summary["initial_corpus"] == 10000.0
    # First installment in list should be the lump sum
    assert result["installments"][0]["type"] == "lump_sum"
    assert result["installments"][0]["invested"] == 10000.0
    assert summary["total_invested"] == 10000.0 + (summary["installments_count"] * 2000.0)


def test_deterministic_across_two_runs(example, synthetic_bars):
    first = json.dumps(
        example.run(symbol="TESTETF", frequency="monthly", bars=synthetic_bars),
        sort_keys=True,
        default=str,
    )
    second = json.dumps(
        example.run(symbol="TESTETF", frequency="monthly", bars=synthetic_bars),
        sort_keys=True,
        default=str,
    )
    assert first == second


def test_calculate_xirr_known_flows(example):
    # Deposit ₹10,000 on Jan 1, ₹10,000 on Feb 1, value ₹22,000 on Jan 1 next year
    flows = [
        (dt.date(2025, 1, 1), -10000.0),
        (dt.date(2025, 2, 1), -10000.0),
        (dt.date(2026, 1, 1), 22000.0),
    ]
    rate = example.calculate_xirr(flows)
    assert rate is not None
    assert 10.0 < rate < 20.0  # Positive annualized return in expected range


def test_main_writes_same_json_to_out(example, tmp_path):
    out = tmp_path / "sip_report.json"
    code = example.main(["--out", str(out), "--format", "json"])
    assert code == 0
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "summary" in data
    assert data["summary"]["symbol"] == "ALPHAETF"


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main([]) == 0
    out = capsys.readouterr().out
    assert "SIP Backtest: ALPHAETF.NSE" in out
    assert "Total Invested" in out
