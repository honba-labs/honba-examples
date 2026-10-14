"""tests/unit/test_10_chained_screener_and_sip.py: Unit tests for chained screener and SIP backtest."""

from __future__ import annotations

import datetime as dt
import importlib.util
import io
import json
from pathlib import Path

import pytest
from honba.strategies.base import Strategy

from honba_examples.base import HonbaExample
from honba_examples.screening import (
    find_catalog_path,
    get_latest_market_date,
    screen_multibaggers,
    screen_value_and_dividend,
)
from honba_examples.sip import calculate_xirr, simulate_portfolio_sip

_PATH = Path(__file__).resolve().parents[2] / "backtesting" / "10_chained_screener_and_sip.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("chained_screener_and_sip", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inheritance(example):
    assert issubclass(example.ChainedScreenerSipExample, HonbaExample)
    assert issubclass(example.SipPortfolioStrategy, Strategy)


def test_sip_strategy_parameter(example):
    strat = example.SipPortfolioStrategy(monthly_amount=15000.0, initial_capital=200000.0)
    assert strat.name == "sip_portfolio_strategy"
    assert strat._monthly_amount == 15000.0
    assert strat._initial_capital == 200000.0


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
        capital_budget=500000.0,
    )
    assert res["summary"]["candidate_count"] == 0
    assert res["summary"]["total_invested"] == 0.0
    assert res["stocks"] == []


def test_screen_multibaggers_returns_data():
    catalog_dir = find_catalog_path()
    candidates = screen_multibaggers(
        catalog_dir=catalog_dir,
        asof_date=dt.date(2023, 1, 1),
        train_years=3,
        fiscal_lag_days=0,  # the real DB is sparse in 2023; the default 90-day lag hides its rows
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
        allow_missing=True,
        fiscal_lag_days=0,  # real fundamentals are sparse at 2023-01-01; missing values now fail
        top=3,
    )
    assert len(candidates) > 0
    c0 = candidates[0]
    assert "symbol" in c0
    assert "pct_from_low" in c0


def test_screen_value_and_dividend_derived_valuation():
    """Value screen computes point-in-time P/E, P/B and dividend yield from fundamentals."""
    catalog_dir = find_catalog_path()
    candidates = screen_value_and_dividend(
        catalog_dir=catalog_dir,
        asof_date=dt.date(2025, 3, 1),
        train_years=3,
        mode="value",
        max_pct_from_52w_low=15.0,
        min_roce=8.0,
        max_debt_equity=1.5,
        max_pe=20.0,
        max_pb=2.0,
        top=8,
    )
    assert len(candidates) > 0
    for c in candidates:
        assert c["pe_ratio"] is not None and c["pe_ratio"] <= 20.0
        assert c["pb_ratio"] is not None and c["pb_ratio"] <= 2.0
        assert "div_yield_pct" in c
        assert "operating_margin_pct" in c
        assert "free_cash_flow" in c


def test_screen_dividend_requires_positive_cash_flow_and_yield():
    """Dividend screen filters to payers with positive FCF when requested."""
    catalog_dir = find_catalog_path()
    candidates = screen_value_and_dividend(
        catalog_dir=catalog_dir,
        asof_date=dt.date(2025, 3, 1),
        train_years=3,
        mode="dividend",
        max_pct_from_52w_low=15.0,
        min_div_yield=3.0,
        min_roce=8.0,
        max_debt_equity=1.5,
        require_positive_fcf=True,
        top=8,
    )
    assert len(candidates) > 0
    for c in candidates:
        assert c["div_yield_pct"] >= 3.0
        assert c["free_cash_flow"] is not None and c["free_cash_flow"] > 0


def test_screen_value_and_dividend_filters_are_applied():
    """The filters must narrow results; a too-strict yield should return fewer/no candidates."""
    catalog_dir = find_catalog_path()
    base = screen_value_and_dividend(
        catalog_dir=catalog_dir,
        asof_date=dt.date(2025, 3, 1),
        mode="dividend",
        max_pct_from_52w_low=15.0,
        min_div_yield=3.0,
        top=20,
    )
    strict = screen_value_and_dividend(
        catalog_dir=catalog_dir,
        asof_date=dt.date(2025, 3, 1),
        mode="dividend",
        max_pct_from_52w_low=15.0,
        min_div_yield=10.0,
        top=20,
    )
    assert len(strict) < len(base)


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
            "--allow-missing",
            "--fiscal-lag-days",
            "0",
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
            "--allow-missing",
            "--fiscal-lag-days",
            "0",
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


def test_main_with_capital_options(example, tmp_path):
    out_file = tmp_path / "capital_report.json"
    code = example.main(
        [
            "--strategy",
            "multibagger",
            "--asof",
            "2023-01-01",
            "--end",
            "2023-06-30",
            "--fiscal-lag-days",
            "0",
            "--capital",
            "500000",
            "--sip-amount",
            "5000",
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
    summary = data["portfolio_backtest"]["summary"]
    assert data["initial_capital"] == 500000.0
    assert summary["sip_amount"] == 5000.0
    assert summary["total_invested"] > 0


# ---------------------------------------------------------------------------
# CLI stream contract (Phase 1): symbol/JSON ingestion from stdin pipes
# ---------------------------------------------------------------------------


def test_parse_symbols_stream_variants(example):
    parse = example.ChainedScreenerSipExample._parse_symbols_stream
    assert parse("") == []
    assert parse("   \n  ") == []
    assert parse("TCS INFY\nRELIANCE") == ["TCS", "INFY", "RELIANCE"]
    assert parse("tcs, infy ,reliance") == ["TCS", "INFY", "RELIANCE"]
    assert parse('["tcs", "infy"]') == ["TCS", "INFY"]
    assert parse('[{"symbol": "tcs"}, {"symbol": "infy"}]') == ["TCS", "INFY"]
    assert parse('{"candidates": [{"symbol": "tcs"}, {"symbol": "infy"}]}') == ["TCS", "INFY"]
    assert parse('{"symbols": ["tcs", "infy"]}') == ["TCS", "INFY"]
    # Malformed JSON falls back to whitespace splitting.
    assert parse("{not json") == ["{NOT", "JSON"]


def test_read_symbols_from_stdin_ignores_tty(example, monkeypatch):
    class _Tty:
        def isatty(self) -> bool:
            return True

        def read(self) -> str:  # pragma: no cover - must never be called
            raise AssertionError("stdin must not be read from a TTY")

    monkeypatch.setattr("sys.stdin", _Tty())
    assert example.ChainedScreenerSipExample._read_symbols_from_stdin() == []


def test_read_symbols_from_stdin_handles_unreadable(example, monkeypatch):
    class _Unreadable:
        def isatty(self) -> bool:
            return False

        def read(self) -> str:
            raise OSError("read on closed stdin")

    monkeypatch.setattr("sys.stdin", _Unreadable())
    assert example.ChainedScreenerSipExample._read_symbols_from_stdin() == []


def test_main_auto_detects_piped_symbols(example, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("TCS INFY\nRELIANCE"))
    code = example.main(["--asof", "2023-01-01", "--end-date", "2023-06-30", "--format", "json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    symbols = {s["symbol"] for s in data["portfolio_backtest"]["stocks"]}
    assert symbols == {"TCS", "INFY", "RELIANCE"}


def test_main_auto_detects_piped_json(example, monkeypatch, capsys):
    payload = '{"asof_date": "2023-01-01", "candidates": [{"symbol": "TCS"}, {"symbol": "INFY"}]}'
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    code = example.main(["--asof", "2023-01-01", "--end-date", "2023-06-30", "--format", "json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    symbols = {s["symbol"] for s in data["portfolio_backtest"]["stocks"]}
    assert symbols == {"TCS", "INFY"}


def test_main_symbols_dash_reads_stdin(example, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("TCS, INFY"))
    code = example.main(
        ["--symbols", "-", "--asof", "2023-01-01", "--end-date", "2023-06-30", "--format", "json"]
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    symbols = {s["symbol"] for s in data["portfolio_backtest"]["stocks"]}
    assert symbols == {"TCS", "INFY"}


def test_main_symbols_dash_empty_stdin_errors(example, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code = example.main(["--symbols", "-", "--asof", "2023-01-01", "--end-date", "2023-06-30"])
    assert code == 1


def test_explicit_symbols_slice_to_top(example, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code = example.main(
        [
            "--symbols",
            "TCS,INFY,RELIANCE",
            "--top",
            "2",
            "--asof",
            "2023-01-01",
            "--end-date",
            "2023-06-30",
            "--format",
            "json",
        ]
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["portfolio_backtest"]["stocks"]) == 2
