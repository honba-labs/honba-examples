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
    assert spec is not None and spec.loader is not None and spec.name is not None
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


def test_summary_prints_the_changes_table_per_rebalance(ex, capsys) -> None:
    """The summary prints the concise Rebalance Schedule table (date, event, added, removed, rebalanced, notional)
    followed by metrics and equity curve, without the final holdings table."""
    days = weekdays(dt.date(2026, 6, 1), 6)
    bars = [bar(s, d, 100.0) for d in days for s in ("AAA", "BBB")]
    result = ex.simulate(bars, ["AAA", "BBB"], start=days[0], capital=100_000.0, rebalance_days=3)
    ex.print_summary(result)
    out = capsys.readouterr().out
    rows = [" ".join(line.replace("│", " ").replace("┃", " ").split()) for line in out.splitlines()]
    assert any("Rebalance Schedule" in r for r in rows)
    assert any("Added (New)" in r for r in rows)
    assert any(days[0].isoformat() in r and "Initial" in r and "+AAA(500)" in r for r in rows)
    assert "Final holdings" not in out


def test_tolerance_band_suppresses_micro_drift_rebalancing(ex) -> None:
    """A position that drifts within the tolerance band does not generate rebalance trades."""
    days = weekdays(dt.date(2026, 6, 1), 6)
    bars = []
    for d in days:
        # AAA moves by +1% (well within 5% band), BBB moves by -1%
        bars.append(bar("AAA", d, 101.0 if d > days[0] else 100.0))
        bars.append(bar("BBB", d, 99.0 if d > days[0] else 100.0))

    # With tolerance_pct=0.05, rebalances after day 0 should generate NO trades
    res_tol = ex.simulate(
        bars, ["AAA", "BBB"], start=days[0], capital=100_000.0, rebalance_days=3, tolerance_pct=0.05
    )
    assert all(len(r["trades"]) == 0 for r in res_tol.rebalances[1:])

    # With tolerance_pct=0.0, rebalances after day 0 DO trade to re-equalize the 1% drift
    res_no_tol = ex.simulate(
        bars, ["AAA", "BBB"], start=days[0], capital=100_000.0, rebalance_days=3, tolerance_pct=0.0
    )
    assert any(len(r["trades"]) > 0 for r in res_no_tol.rebalances[1:])


def test_residual_cash_sweep_and_unaffordable_stock_handling(ex) -> None:
    """Unaffordable stocks are not allocated target cash, and sweep minimizes uninvested cash."""
    qty: dict[str, int] = {}
    closes = {"EXPENSIVE": 60_000.0, "CHEAP": 100.0}
    cash = 10_000.0
    fee = 0.001

    # EXPENSIVE is 60,000, cash is 10,000. EXPENSIVE is unaffordable.
    # Target should be allocated solely to CHEAP, buying floor(10,000 / 100.1) = 99 shares.
    cash_left, trades = ex.rebalance_orders(qty, closes, cash, fee, is_day0=True)
    assert len(trades) == 1
    assert qty["EXPENSIVE"] == 0
    assert qty["CHEAP"] > 0
    # Remainder cash is less than the price of CHEAP
    assert cash_left < closes["CHEAP"] * (1 + fee)


def test_simresult_margin_parameters(ex) -> None:
    """SimResult dataclass has margin attributes and defaults."""
    res = ex.SimResult()
    assert res.margin_used_curve == []
    assert res.max_margin_used_pct == 0.0
    assert res.margin_calls == 0
    assert res.leverage == 1.0
    assert res.initial_margin == ex.DEFAULT_INITIAL_MARGIN
    assert res.maintenance_margin == ex.DEFAULT_MAINTENANCE_MARGIN


def test_buy_shortfall_supports_margin_buying(ex) -> None:
    """_buy_shortfall allows purchasing with borrowed cash when max_borrow > 0."""
    from honba_examples.ewr import _buy_shortfall

    qty: dict[str, int] = {"AAA": 0}
    desired = {"AAA": 100}
    closes = {"AAA": 100.0}
    fee = 0.001

    # Cash is zero, but max_borrow allows buying on margin
    trades, cash_after = _buy_shortfall(
        "AAA", qty, desired, closes, cash=0.0, fee=fee, max_borrow=10_010.0
    )
    assert len(trades) == 1
    assert trades[0]["qty"] == 100
    assert qty["AAA"] == 100
    assert cash_after < 0  # negative cash indicates margin borrowing


def test_sell_excess_supports_short_selling(ex) -> None:
    """_sell_excess allows short selling into negative positions when allow_short=True."""
    from honba_examples.ewr import _sell_excess

    qty: dict[str, int] = {"AAA": 0}
    desired = {"AAA": -50}
    closes = {"AAA": 100.0}
    fee = 0.001

    # When allow_short=False, cannot short sell
    trades_long_only, cash1 = _sell_excess(
        "AAA", dict(qty), desired, closes, cash=1000.0, fee=fee, allow_short=False
    )
    assert trades_long_only == []
    assert cash1 == 1000.0

    # When allow_short=True, short selling creates negative position and raises cash
    trades_short, cash2 = _sell_excess(
        "AAA", qty, desired, closes, cash=1000.0, fee=fee, allow_short=True
    )
    assert len(trades_short) == 1
    assert trades_short[0]["qty"] == 50
    assert qty["AAA"] == -50
    assert cash2 > 1000.0  # sale proceeds credited


def test_simulate_margin_call_check(ex) -> None:
    """A severe market crash with 2x leverage triggers margin calls."""
    days = weekdays(dt.date(2026, 6, 1), 6)
    bars = []
    # Day 0 starts at 100.0, then price crashes to 20.0
    for d in days:
        px = 100.0 if d == days[0] else 20.0
        bars.append(bar("AAA", d, px))

    res = ex.simulate(
        bars,
        ["AAA"],
        start=days[0],
        capital=100_000.0,
        rebalance_days=30,
        leverage=2.0,
        maintenance_margin=0.25,
    )
    # Severe drop with 2x leverage leads to equity < maintenance margin requirement
    assert res.margin_calls > 0
    assert res.max_margin_used_pct > 0.0


def test_cli_margin_arguments(ex) -> None:
    """Alpha30EWRExample parses margin CLI arguments properly."""
    example = ex.Alpha30EWRExample()
    args = example.parse_args([
        "--leverage", "2.5",
        "--initial-margin", "0.4",
        "--maintenance-margin", "0.2",
        "--allow-short",
    ])
    assert args.leverage == 2.5
    assert args.initial_margin == 0.4
    assert args.maintenance_margin == 0.2
    assert args.allow_short is True
    assert example.leverage == 2.5
    assert example.initial_margin == 0.4
    assert example.maintenance_margin == 0.2
    assert example.allow_short is True
