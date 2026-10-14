"""honba_examples.ewr.simulate: missing-data reporting and the margin caveat."""

from __future__ import annotations

import datetime as dt

from honba_examples.ewr import simulate
from honba_examples.ewr.model import SimResult
from honba_examples.synthetic import bar, weekdays

START = dt.date(2026, 1, 5)


def _bars(symbols, n=40, step=1.0):
    return [
        bar(sym, d, 100.0 + k * 10 + i * step)
        for k, sym in enumerate(symbols)
        for i, d in enumerate(weekdays(START, n))
    ]


def test_symbols_without_bars_are_reported_missing():
    res = simulate(_bars(["AAA", "BBB"]), ["AAA", "BBB", "CCC"], start=START, rebalance_days=7)
    assert res.missing_data == ["CCC"]
    assert set(res.final_holdings) == {"AAA", "BBB"}


def test_all_symbols_missing_returns_them_all():
    res = simulate(_bars(["AAA"]), ["XXX", "YYY"], start=START)
    assert res.missing_data == ["XXX", "YYY"]
    assert res.equity_curve == []


def test_no_missing_symbols_means_an_empty_list():
    res = simulate(_bars(["AAA", "BBB"]), ["AAA", "BBB"], start=START, rebalance_days=7)
    assert res.missing_data == []


def test_leveraged_run_states_that_margin_calls_do_not_liquidate_or_charge_interest():
    res = simulate(
        _bars(["AAA", "BBB"]), ["AAA", "BBB"], start=START, rebalance_days=7, leverage=2.0
    )
    text = " ".join(res.notes)
    assert "never liquidates" in text and "interest" in text


def test_unleveraged_run_has_no_margin_note():
    res = simulate(_bars(["AAA", "BBB"]), ["AAA", "BBB"], start=START, rebalance_days=7)
    assert res.notes == []
    assert isinstance(res, SimResult)
