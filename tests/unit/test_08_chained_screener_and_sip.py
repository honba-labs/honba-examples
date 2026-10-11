"""tests/unit/test_08_chained_screener_and_sip.py: Unit tests for chained screener and SIP backtest."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

from honba_examples.screening import (
    find_catalog_path,
    get_latest_market_date,
    screen_multibaggers,
    screen_value_and_dividend,
)
from honba_examples.sip import calculate_xirr, simulate_portfolio_sip

_PATH = Path(__file__).resolve().parents[2] / "backtesting" / "08_chained_screener_and_sip.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("chained_screener_and_sip", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_calculate_xirr_known_flows():
    flows = [
        (dt.date(2025, 1, 1), -10000.0),
        (dt.date(2025, 2, 1), -10000.0),
        (dt.date(2026, 1, 1), 22000.0),
    ]
    rate = calculate_xirr(flows)
    assert rate is not None
    assert 10.0 < rate < 20.0


def test_calculate_xirr_invalid_flows():
    assert calculate_xirr([]) is None
    assert calculate_xirr([(dt.date(2025, 1, 1), -100.0)]) is None
    assert calculate_xirr([(dt.date(2025, 1, 1), 100.0), (dt.date(2025, 2, 1), 200.0)]) is None


def test_simulate_portfolio_sip_empty():
    catalog_dir = find_catalog_path()
    res = simulate_portfolio_sip(
        catalog_dir=catalog_dir,
        symbols=[],
        start_date=dt.date(2023, 1, 1),
        end_date=dt.date(2023, 6, 1),
    )
    assert res["summary"]["candidate_count"] == 0
    assert res["stocks"] == []


def test_screen_multibaggers_returns_data():
    catalog_dir = find_catalog_path()
    candidates = screen_multibaggers(
        catalog_dir=catalog_dir,
        asof_date=dt.date(2023, 1, 1),
        train_years=3,
        top=3,
    )
    assert len(candidates) > 0
    c0 = candidates[0]
    assert "symbol" in c0
    assert "ret_1y" in c0
    assert "train_ret_pct" in c0
    assert c0["close"] > 0


def test_screen_value_and_dividend_returns_data():
    catalog_dir = find_catalog_path()
    candidates = screen_value_and_dividend(
        catalog_dir=catalog_dir,
        asof_date=dt.date(2023, 1, 1),
        train_years=3,
        mode="value",
        top=3,
    )
    assert len(candidates) > 0
    c0 = candidates[0]
    assert "symbol" in c0
    assert "pct_from_low" in c0


def test_get_latest_market_date():
    catalog_dir = find_catalog_path()
    latest_date = get_latest_market_date(catalog_dir)
    assert isinstance(latest_date, dt.date)
    assert latest_date.year >= 2024


def test_main_screen_only_json(example, capsys):
    code = example.main(
        [
            "--strategy",
            "multibagger",
            "--asof",
            "2023-01-01",
            "--screen-only",
            "--top",
            "2",
            "--format",
            "json",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["stage"] == "screening"
    assert len(data["candidates"]) == 2


def test_main_chained_json_output(example, tmp_path):
    out_file = tmp_path / "chained_report.json"
    code = example.main(
        [
            "--strategy",
            "multibagger",
            "--asof",
            "2023-01-01",
            "--end-date",
            "2023-12-31",
            "--top",
            "2",
            "--format",
            "json",
            "--out",
            str(out_file),
        ]
    )
    assert code == 0
    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert "screening_candidates" in data
    assert "portfolio_backtest" in data
    assert len(data["portfolio_backtest"]["stocks"]) == 2
