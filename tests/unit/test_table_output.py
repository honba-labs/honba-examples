"""The universe and backtest examples print their tables through ``honba.display``.

Each test runs the script's output function on synthetic data and checks the rendered text
carries the table headers and the expected values (no ANSI: pytest's capture is not a TTY).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2] / "universes"


def _load(filename: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")


def test_nifty50_script_prints_a_members_table(capsys: pytest.CaptureFixture[str]) -> None:
    _load("01_nifty50_constituents.py", "ex_nifty50").main()
    out = capsys.readouterr().out
    for needle in ("Universe", "nifty50", "Exchange", "NSE", "Members", "Symbol", "RELIANCE"):
        assert needle in out
    assert "\x1b[" not in out


def test_custom_universe_prints_members_table(capsys: pytest.CaptureFixture[str]) -> None:
    module = _load("05_alpha30_custom_universe.py", "ex_custom")
    module.main()
    out = capsys.readouterr().out
    assert "Custom universe" in out and "Symbol" in out and "Exchange" in out
    assert module.CUSTOM_SYMBOLS[0] in out


def test_alpha30_constituents_print_members(capsys: pytest.CaptureFixture[str]) -> None:
    module = _load("alpha30_constituents.py", "ex_a30")
    members = module.load_alpha30()
    module.print_members(members)
    out = capsys.readouterr().out
    assert f"{len(members)} instruments" in out
    assert "Symbol" in out and "Exchange" in out and members[0].symbol in out


def test_07_summary_is_a_metrics_table(capsys: pytest.CaptureFixture[str]) -> None:
    module = _load("07_alpha30_backtest.py", "ex07_tbl")
    cfg = {
        "test_start": "2026-05-18",
        "test_end": "2026-06-30",
        "exchange": "NSE",
        "settlement_days": 1,
        "capital_minor": 100_000_000,
    }
    metrics = {
        "final_equity_minor": 101_250_000,
        "final_cash_minor": 5_000_000,
        "total_return_pct": 1.25,
        "cagr_pct": 9.5,
        "max_drawdown_pct": -2.5,
        "sharpe": 1.2345,
        "turnover": 0.5,
        "avg_cash_pct": 3.0,
        "total_fees_minor": 12_345,
        "n_fills": 30,
        "n_released_unfunded": 2,
        "n_unfilled_at_end": 1,
    }
    out = {"config": cfg, "result": {"metrics": metrics}, "run_hash": "abc123"}
    module.print_summary(out, SimpleNamespace(test_sessions=30, warmup_sessions=10))
    text = capsys.readouterr().out
    for needle in ("Metric", "Value", "Final equity", "1,012,500.00", "Sharpe", "1.234", "Fills"):
        assert needle in text
    assert "run_hash abc123" in text
    assert "2026-05-18" in text and "30 test, 10 warm-up" in text


def test_08_summary_is_a_metrics_table(capsys: pytest.CaptureFixture[str]) -> None:
    module = _load("08_alpha30_union_ewr_backtest.py", "ex08_tbl")
    result = SimpleNamespace(
        metrics={
            "final_value": 1_012_500.5,
            "total_return_pct": 1.25,
            "cagr_pct": 9.5,
            "max_drawdown_pct": -2.5,
            "sharpe": 1.23,
            "total_fees": 123.45,
            "turnover": 0.5,
            "avg_cash_pct": 3.0,
            "n_fills": 30,
            "n_rebalances": 3,
        },
        missing_data=["IDEA"],
        never_held=["MRF"],
        rebalances=[
            {
                "date": "2026-06-01",
                "trades": [
                    {
                        "symbol": "AAA",
                        "side": "buy",
                        "qty": 3,
                        "price": 10.0,
                        "notional": 30.0,
                        "fee": 0.1,
                    },
                    {
                        "symbol": "BBB",
                        "side": "sell",
                        "qty": 2,
                        "price": 20.0,
                        "notional": 40.0,
                        "fee": 0.2,
                    },
                ],
            }
        ],
        final_holdings={"AAA": 3},
        equity_curve=[
            {"date": "2026-06-01", "value": 1_000_000.0, "cash": 30_000.0},
            {"date": "2026-06-02", "value": 1_012_500.5, "cash": 30_000.0},
        ],
    )
    module.print_summary(result)
    text = capsys.readouterr().out
    for needle in (
        "Metric",
        "Value",
        "Final value",
        "1,012,500.50",
        "N rebalances",
        "IDEA",
        "MRF",
        "2026-06-01",
        "Net shares",
        "+3",
        "-2",
        "Equity curve",
    ):
        assert needle in text
