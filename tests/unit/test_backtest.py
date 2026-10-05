"""Portfolio backtest loop: warm-up gating, next-open fills, test-window-only accounting."""

from __future__ import annotations

import datetime as dt

import pytest
from honba.domain.bar import Bar
from honba.strategies.base import Strategy

from honba_examples.backtest import run_portfolio_backtest
from honba_examples.money import notional_paise
from tests.synthetic import bar, weekdays

DAYS = weekdays(dt.date(2026, 6, 1), 6)  # Mon 1 .. Mon 8 June 2026
SYMS = ("AAA", "BBB")


def zero_cost(side, qty, px) -> int:
    return 0


class BuyEveryBar(Strategy):
    """Buys one share of each instrument on every bar it is not already waiting on."""

    name = "buy_every_bar"

    def __init__(self) -> None:
        self.seen: list[Bar] = []

    def on_bar(self, bar: Bar) -> None:
        self.seen.append(bar)
        if not self.busy(bar.instrument_id):
            self.buy(bar.instrument_id, 1)


def series(open_step: float = 1.0) -> list[Bar]:
    out = []
    for i, d in enumerate(DAYS):
        for j, s in enumerate(SYMS):
            o = 100.0 + 10 * j + i * open_step
            out.append(bar(s, d, o, close=o + 0.5))
    return out


def run(strategy: Strategy, warmup: int = 2, **kw):
    params = {
        "test_start": DAYS[warmup],
        "test_end": DAYS[-1],
        "capital_paise": 1_000_000,
        "settlement_days": 0,
        "cost_paise": zero_cost,
    }
    params.update(kw)
    return run_portfolio_backtest(strategy, series(), **params)


def test_warmup_bars_reach_the_strategy_but_never_trade() -> None:
    strat = BuyEveryBar()
    result = run(strat, warmup=2)
    assert len(strat.seen) == len(DAYS) * len(SYMS)  # warm-up bars still fed (indicators)
    suppressed = [e for e in result.order_events if e.kind == "suppressed_warmup"]
    assert {e.date for e in suppressed} == {DAYS[0].isoformat(), DAYS[1].isoformat()}
    # First test-window decision is on DAYS[2]; it fills at the DAYS[3] open.
    assert min(f.date for f in result.fills) == DAYS[3].isoformat()
    assert result.curve[0]["date"] == DAYS[2].isoformat()
    assert result.warmup_sessions == 2
    assert result.test_sessions == 4


def test_fills_are_at_the_next_sessions_open() -> None:
    result = run(BuyEveryBar(), warmup=0)
    by_day = {d.isoformat(): i for i, d in enumerate(DAYS)}
    for f in result.fills:
        i = by_day[f.date]
        j = SYMS.index(f.symbol)
        assert f.price == 100.0 + 10 * j + i  # the open of the fill session
        assert by_day[f.submitted_date] == i - 1


def test_fees_and_turnover_count_only_test_window_fills() -> None:
    def flat_fee(side, qty, px) -> int:
        return 1_000  # 10 rupees per fill

    gated = run(BuyEveryBar(), warmup=2, cost_paise=flat_fee)
    assert gated.fees_paise == 1_000 * len(gated.fills)
    assert gated.traded_notional_paise == sum(f.notional_paise for f in gated.fills)
    assert all(f.date >= DAYS[2].isoformat() for f in gated.fills)
    m = gated.metrics()
    assert m["n_fills"] == len(gated.fills)
    assert m["total_fees_paise"] == gated.fees_paise


def test_curve_is_cash_plus_positions_marked_at_close_in_paise() -> None:
    result = run(BuyEveryBar(), warmup=0)
    last = result.curve[-1]
    i = len(DAYS) - 1
    held = {s: result.final_positions[s] for s in SYMS}
    marks = sum(notional_paise(held[s], 100.0 + 10 * j + i + 0.5) for j, s in enumerate(SYMS))
    assert last["positions_value_paise"] == marks
    assert last["equity_paise"] == last["cash_paise"] + marks
    assert all(isinstance(p["equity_paise"], int) for p in result.curve)


def test_bars_outside_the_window_are_not_traded() -> None:
    result = run(BuyEveryBar(), warmup=0, test_end=DAYS[2])
    assert [p["date"] for p in result.curve] == [d.isoformat() for d in DAYS[:3]]
    assert max(f.date for f in result.fills) <= DAYS[2].isoformat()


def test_empty_test_window_is_an_error() -> None:
    with pytest.raises(ValueError, match="no bars"):
        run(BuyEveryBar(), test_start=dt.date(2027, 1, 1), test_end=dt.date(2027, 2, 1))


def test_runs_are_deterministic() -> None:
    a = run(BuyEveryBar())
    b = run(BuyEveryBar())
    assert a.to_dict() == b.to_dict()


class BuyAllOnFirstBar(Strategy):
    """Submits one buy per symbol in a caller-chosen order, like iterating a set."""

    name = "buy_all"

    def __init__(self, order: tuple[str, ...]) -> None:
        self.order = order
        self.done = False

    def on_bar(self, bar: Bar) -> None:
        if not self.done and bar.instrument_id.symbol == SYMS[-1]:
            self.done = True
            for s in self.order:
                self.buy(type(bar.instrument_id)(s, "NSE"), 1)


def test_intent_order_within_an_event_does_not_change_the_run() -> None:
    a = run(BuyAllOnFirstBar(SYMS), warmup=0)
    b = run(BuyAllOnFirstBar(tuple(reversed(SYMS))), warmup=0)
    assert a.to_dict() == b.to_dict()
