"""strategies/14_partial_fill_handling: partial fill and order event unit tests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderSide
from honba.entities.trade import Trade

_PATH = (
    Path(__file__).resolve().parents[2] / "examples" / "strategies" / "14_partial_fill_handling.py"
)


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("partial_fill_handling", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["partial_fill_handling"] = module
    spec.loader.exec_module(module)
    return module


def test_run_returns_expected_structure(example):
    result = example.run(bars=20, target_quantity=20.0)
    assert set(result) == {"strategy", "config", "result"}
    assert result["strategy"]["name"] == "partial_fill_handling"
    assert result["config"]["target_quantity"] == 20.0
    res = result["result"]
    assert "order_events" in res
    assert "filled_quantity" in res
    assert "remaining_quantity" in res


def test_order_events_captured_in_run(example):
    result = example.run(bars=20, target_quantity=15.0)
    events = result["result"]["order_events"]
    assert len(events) >= 2
    event_types = [e["event_type"] for e in events]
    assert "SUBMITTED" in event_types
    assert "ACCEPTED" in event_types


def test_partial_fill_and_cumulative_accounting(example):
    strat = example.PartialFillStrategy(target_quantity=100.0)
    inst = InstrumentId("RELIANCE", "NSE")

    # First partial fill: 30 shares
    fill1 = Trade(inst, OrderSide.BUY, 30.0, 2500.0, 1000, "ord-1")
    strat.on_fill(fill1)
    assert strat.filled_quantity == 30.0
    assert strat.remaining_quantity == 70.0
    assert strat.events_log[-1]["event_type"] == "PARTIAL_FILL"

    # Second partial fill: 40 shares
    fill2 = Trade(inst, OrderSide.BUY, 40.0, 2505.0, 2000, "ord-1")
    strat.on_fill(fill2)
    assert strat.filled_quantity == 70.0
    assert strat.remaining_quantity == 30.0
    assert strat.events_log[-1]["event_type"] == "PARTIAL_FILL"

    # Final fill: 30 shares
    fill3 = Trade(inst, OrderSide.BUY, 30.0, 2510.0, 3000, "ord-1")
    strat.on_fill(fill3)
    assert strat.filled_quantity == 100.0
    assert strat.remaining_quantity == 0.0
    assert strat.events_log[-1]["event_type"] == "FILLED"


def test_order_modification_flow(example):
    strat = example.PartialFillStrategy(target_quantity=50.0)
    dummy_bar = Bar(InstrumentId("RELIANCE", "NSE"), 1000, 2500.0, 2505.0, 2495.0, 2500.0, 1000.0)
    strat.active_order_id = "initial-1"
    strat.active_order_qty = 50.0
    strat.active_order_price = 2490.0

    strat.modify_order(dummy_bar, new_price=2502.0, new_qty=50.0)

    event_types = [e["event_type"] for e in strat.events_log]
    assert "CANCELLED" in event_types
    assert "MODIFIED" in event_types
    assert strat.active_order_price == 2502.0


def test_main_happy_path_returns_zero(example, tmp_path, capsys):
    out = tmp_path / "partial_fill.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    assert file_json["strategy"]["name"] == "partial_fill_handling"
    assert "partial_fill_handling" in stdout
