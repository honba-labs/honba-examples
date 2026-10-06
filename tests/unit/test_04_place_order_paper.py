"""basic/04_place_order_paper: intent -> report, fills, a cancel, a reject, the books."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "basic" / "04_place_order_paper.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("place_order_paper", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def report(example):
    return example.run(adapter="fake")


def test_result_shape_reports_intents_reports_and_books(report):
    assert set(report) == {
        "adapter",
        "product",
        "market_buy",
        "idempotent_repeat",
        "resting_limit",
        "rejected_buy",
        "orders",
        "trades",
        "funds",
    }
    assert report["adapter"] == "fake"
    assert report["product"] == "delivery"
    assert set(report["funds"]) >= {"available_cash", "opening_balance", "currency"}


def test_market_buy_fills_at_the_ask(report):
    intent = report["market_buy"]["intent"]
    assert set(intent) >= {"instrument_id", "side", "quantity", "order_type", "price"}
    assert intent["instrument_id"] == {"symbol": "RELIANCE", "exchange": "NSE"}
    assert (intent["side"], intent["quantity"], intent["order_type"]) == ("buy", 10, "market")
    assert intent["price"] is None

    fill = report["market_buy"]["report"]
    assert fill["order_id"] == "reliance-buy"
    assert fill["status"] == "filled"
    assert fill["product"] == "delivery"
    assert fill["quantity"] == 10
    assert fill["filled_quantity"] == 10
    assert fill["average_price"] == 2450.55  # the fake's first ask for RELIANCE
    assert fill["reject_reason"] is None
    assert fill["ts_event"] > 0


def test_non_marketable_limit_rests_then_cancels(report):
    limit = report["resting_limit"]
    assert limit["intent"]["order_type"] == "limit"
    assert limit["intent"]["price"] == 2400.0

    accepted = limit["accepted"]
    assert accepted["status"] == "accepted"
    assert accepted["price"] == 2400.0
    assert accepted["filled_quantity"] == 0.0
    assert accepted["reject_reason"] is None

    cancelled = limit["cancelled"]
    assert cancelled["order_id"] == accepted["order_id"]
    assert cancelled["status"] == "cancelled"
    assert cancelled["filled_quantity"] == 0.0
    assert cancelled["price"] == 2400.0


def test_oversized_buy_is_rejected_as_data_not_an_exception(report):
    # place_order returned this report; a refusal is a field, never a raised exception.
    rejected = report["rejected_buy"]["report"]
    assert report["rejected_buy"]["intent"]["quantity"] == 1000
    assert rejected["status"] == "rejected"
    assert rejected["reject_reason"] == "insufficient funds"
    assert rejected["filled_quantity"] == 0.0
    assert rejected["average_price"] == 0.0


def test_client_order_id_makes_placement_idempotent(report):
    repeat = report["idempotent_repeat"]
    assert repeat["client_order_id"] == "reliance-buy"
    assert repeat["same_report"] is True
    assert repeat["order_count"] == 1
    assert repeat["trade_count"] == 1
    assert report["market_buy"]["report"]["order_id"] == "reliance-buy"


def test_order_and_trade_books_reflect_the_fill(report):
    orders = report["orders"]
    assert [o["order_id"] for o in orders] == [
        "reliance-buy",
        "reliance-limit",
        "tcs-oversized",
    ]
    assert [o["status"] for o in orders] == ["filled", "cancelled", "rejected"]

    trades = report["trades"]
    assert len(trades) == 1
    trade = trades[0]
    assert trade["instrument_id"] == {"symbol": "RELIANCE", "exchange": "NSE"}
    assert (trade["side"], trade["quantity"], trade["price"]) == ("buy", 10, 2450.55)
    assert trade["order_id"] == "reliance-buy"


def test_available_cash_moved_by_exactly_the_fill_notional(report):
    fill = report["market_buy"]["report"]
    funds = report["funds"]
    assert funds["opening_balance"] == 1_000_000.0
    assert funds["available_cash"] == 1_000_000.0 - fill["quantity"] * fill["average_price"]
    assert funds["available_cash"] == 975_494.5
    assert funds["margin_used"] == 0.0


def test_output_is_deterministic_across_two_calls(example):
    first = json.dumps(example.run(adapter="fake"))
    second = json.dumps(example.run(adapter="fake"))
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "paper_orders.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    assert stdout == out.read_text()
    assert json.loads(stdout)["market_buy"]["report"]["status"] == "filled"
