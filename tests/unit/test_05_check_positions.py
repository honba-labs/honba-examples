"""basic/05_check_positions: the account books after a round trip, capabilities as data."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "basic" / "05_check_positions.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("check_positions", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def report(example):
    return example.run(adapter="fake")


def test_result_shape_reports_positions_funds_trades_and_holdings(report):
    assert set(report) == {
        "adapter",
        "position_after_buy",
        "positions",
        "flat_omitted",
        "holdings",
        "funds",
        "trades",
    }
    assert report["adapter"] == "fake"
    assert set(report["funds"]) >= {"available_cash", "opening_balance", "currency"}
    assert report["trades"]


def test_buy_opens_a_position_with_side_quantity_and_avg_price(report):
    row = report["position_after_buy"]
    assert row["instrument_id"] == {"symbol": "RELIANCE", "exchange": "NSE"}
    assert row["currency"] == "INR"
    assert row["side"] == "long"
    assert row["quantity"] == 10
    assert row["avg_price"] == 2450.55
    assert row["realized_pnl"] == {"amount": 0, "currency": "INR"}
    assert row["realized_pnl_rupees"] == 0.0


def test_partial_sell_reduces_the_position_and_books_realized_pnl(report):
    rows = {row["instrument_id"]["symbol"]: row for row in report["positions"]}
    reliance = rows["RELIANCE"]
    assert reliance["side"] == "long"
    assert reliance["quantity"] == 6  # 10 bought, 4 sold
    assert reliance["avg_price"] == 2450.55  # the buy price survives a partial close
    # 4 shares sold at the bid after buying at the ask: the spread is the realized loss,
    # booked once, in integer paise (ADR 0011).
    assert reliance["realized_pnl"] == {"amount": -40, "currency": "INR"}
    assert reliance["realized_pnl_rupees"] == -0.4


def test_flat_position_is_omitted_from_the_book(report):
    flat = report["flat_omitted"]
    assert flat["instrument"] == {"symbol": "TCS", "exchange": "NSE"}
    assert flat["bought"] == 5
    assert flat["sold"] == 5
    assert flat["in_positions"] is False

    assert [row["instrument_id"]["symbol"] for row in report["positions"]] == ["RELIANCE"]
    traded = {trade["instrument_id"]["symbol"] for trade in report["trades"]}
    assert traded == {"RELIANCE", "TCS"}


def test_funds_and_trades_tell_the_same_story(report):
    trades = report["trades"]
    assert [(t["side"], t["quantity"]) for t in trades] == [
        ("buy", 10),
        ("sell", 4),
        ("buy", 5),
        ("sell", 5),
    ]
    cash = 1_000_000.0
    for trade in trades:
        notional = trade["quantity"] * trade["price"]
        cash = cash + (notional if trade["side"] == "sell" else -notional)
    assert report["funds"]["available_cash"] == cash
    assert report["funds"]["available_cash"] == 985_295.3


def test_holdings_is_reported_as_unsupported_without_an_exception(report):
    holdings = report["holdings"]
    assert holdings["supported"] is False
    assert holdings["capability"] == "holdings"
    assert "holdings" in holdings["reason"]


def test_output_is_deterministic_across_two_calls(example):
    first = json.dumps(example.run(adapter="fake"))
    second = json.dumps(example.run(adapter="fake"))
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "positions.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    assert stdout == out.read_text()
    assert json.loads(stdout)["positions"]
