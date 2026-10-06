"""08: each rebalance reports share additions and sell-offs derived from its actual fills."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

from tests.synthetic import bar, weekdays

_PATH = Path(__file__).resolve().parents[2] / "universes" / "08_alpha30_union_ewr_backtest.py"


@pytest.fixture(scope="module")
def ex():
    spec = importlib.util.spec_from_file_location("ex08_adds", _PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_format_changes_sorts_symbols_and_prints_empty_lists(ex) -> None:
    assert (
        ex.format_changes({"ZED": 10, "AAA": 3}, {"MID": 5})
        == "adds [AAA+3, ZED+10]  sells [MID-5]"
    )
    assert ex.format_changes({}, {}) == "adds []  sells []"


def test_changes_are_net_fill_quantities_per_symbol(ex) -> None:
    trades = [
        {"symbol": "B", "side": "sell", "qty": 4},
        {"symbol": "A", "side": "buy", "qty": 2},
        {"symbol": "A", "side": "buy", "qty": 1},
    ]
    assert ex.trade_changes(trades) == ({"A": 3}, {"B": 4})


@pytest.mark.parametrize("settlement_days", [0, 2])
def test_simulate_reports_changes_matching_its_fills(ex, settlement_days: int) -> None:
    days = weekdays(dt.date(2026, 6, 1), 12)
    bars = []
    for i, d in enumerate(days):
        bars.append(bar("AAA", d, 100.0))
        bars.append(bar("BBB", d, 100.0 + 15 * i))  # BBB rallies, so it is sold down
    result = ex.simulate(
        bars,
        ["AAA", "BBB"],
        start=days[0],
        capital=100_000.0,
        rebalance_days=3,
        settlement_days=settlement_days,
    )
    changes = result.rebalance_changes
    assert changes[0]["date"] == days[0].isoformat()
    assert changes[0]["adds"] == {"AAA": 500, "BBB": 499}  # day 0 buys the basket; fee caps BBB
    assert changes[0]["sells"] == {}
    assert any(c["sells"].get("BBB") for c in changes[1:])
    # Derived from fills: summed changes reproduce the final holdings.
    held: dict[str, int] = {}
    for c in changes:
        for s, q in c["adds"].items():
            held[s] = held.get(s, 0) + q
        for s, q in c["sells"].items():
            held[s] = held.get(s, 0) - q
    assert {s: q for s, q in held.items() if q} == result.final_holdings


def test_summary_prints_one_line_per_rebalance(ex, capsys) -> None:
    days = weekdays(dt.date(2026, 6, 1), 6)
    bars = [bar(s, d, 100.0) for d in days for s in ("AAA", "BBB")]
    result = ex.simulate(bars, ["AAA", "BBB"], start=days[0], capital=100_000.0, rebalance_days=3)
    ex.print_summary(result)
    out = capsys.readouterr().out
    assert f"{days[0].isoformat()}  adds [AAA+" in out
    assert "sells []" in out
