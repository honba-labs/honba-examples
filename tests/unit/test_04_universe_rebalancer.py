"""04: wall-clock-free bar dates, point-in-time membership, fresh prices, equity sizing."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest
from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.markets.india import universes as core
from honba.strategies.context import LedgerContext

_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "universes"
    / "04_alpha30_generic_rebalancer.py"
)
NS_DAY = 86_400 * 1_000_000_000


@pytest.fixture(scope="module")
def ex():
    # 04_alpha30_generic_rebalancer.py imports its sibling `alpha30_constituents` module by bare
    # name (it works unmodified when run directly: `python` prepends a script's own directory to
    # sys.path). spec_from_file_location + exec_module does not do that, so add the script's
    # directory here, mirroring test_mutual_funds_03_imports.py's sys.path handling.
    script_dir = str(_PATH.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location("ex04_test", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ts(day: dt.date) -> int:
    return int(
        dt.datetime(day.year, day.month, day.day, 10, tzinfo=dt.timezone.utc).timestamp()
    ) * (10**9)


def _bar(sym: str, day: dt.date, close: float = 100.0) -> Bar:
    return Bar(InstrumentId(sym, "NSE"), _ts(day), close, close, close, close, 1000.0)


@pytest.fixture
def tiny_history():
    name = "tiny_pit_universe"
    core.UNIVERSES[name] = ("AAA", "BBB")
    core.register_universe_history(
        core.UniverseHistory(
            name,
            (
                core.Constituent("AAA", dt.date(2020, 1, 1)),
                core.Constituent("BBB", dt.date(2020, 1, 1), dt.date(2024, 1, 10)),
                core.Constituent("CCC", dt.date(2024, 1, 10)),
            ),
        ),
        replace=True,
    )
    yield name
    core.UNIVERSES.pop(name, None)
    core._HISTORY_REGISTRY.pop(name, None)


def _strategy(ex, name, cash=1_000_000.0, **kw):
    s = ex.UniverseEqualWeightRebalance(universe=name, rebalance_days=1, **kw)
    ledger = LedgerContext(cash=cash)
    s.bind(ledger)
    return s, ledger


def test_bar_day_without_timestamp_raises_not_wall_clock(ex):
    class NoTs:
        ts = None

    with pytest.raises(ValueError, match="wall clock"):
        ex._bar_day(NoTs(), 0)
    assert ex._bar_day(NoTs(), _ts(dt.date(2024, 1, 5))) == dt.date(2024, 1, 5)


def test_membership_is_point_in_time(ex, tiny_history):
    s, _ = _strategy(ex, tiny_history)
    assert {i.symbol for i in s._resolve(dt.date(2023, 6, 1))} == {"AAA", "BBB"}
    assert {i.symbol for i in s._resolve(dt.date(2024, 2, 1))} == {"AAA", "CCC"}


def test_no_history_falls_back_to_static_with_warning(ex, capsys):
    s, _ = _strategy(ex, "nifty50")
    assert len(s._resolve(dt.date(2024, 2, 1))) == 50
    assert "no point-in-time history" in capsys.readouterr().out


def test_rebalance_exits_leaver_and_enters_joiner(ex, tiny_history):
    s, ledger = _strategy(ex, tiny_history)
    s.on_start()
    d1, d2 = dt.date(2024, 1, 8), dt.date(2024, 1, 12)
    for sym in ("AAA", "BBB", "CCC"):
        s.on_bar(_bar(sym, d1))
    ledger.drain_intents()
    ledger._pending.clear()  # the initial buys count as filled
    ledger._positions[InstrumentId("BBB", "NSE")] = 50.0
    for sym in ("AAA", "BBB", "CCC"):
        s.on_bar(_bar(sym, d2))
    intents = ledger.drain_intents()
    sides = {(i.instrument_id.symbol, i.side.name.lower()) for i in intents}
    assert ("BBB", "sell") in sides
    assert ("CCC", "buy") in sides


def test_stale_price_is_skipped(ex, tiny_history):
    s, ledger = _strategy(ex, tiny_history, max_price_age_days=2)
    s.on_start()
    s.on_bar(_bar("AAA", dt.date(2024, 1, 1)))  # first bar: only AAA ever prints
    ledger.drain_intents()
    s._rebalance("t", dt.date(2024, 1, 20))
    assert ledger.drain_intents() == []  # AAA's close is 19 days old


def test_sizing_uses_current_equity_not_constructor_capital(ex, tiny_history):
    s, ledger = _strategy(ex, tiny_history, cash=200_000.0, capital=1_000_000.0)
    assert s._equity() == pytest.approx(200_000.0)
    s.on_start()
    s._last_prices[InstrumentId("AAA", "NSE")] = 100.0
    s._price_day[InstrumentId("AAA", "NSE")] = dt.date(2024, 1, 8)
    s._rebalance("t", dt.date(2024, 1, 8))
    buys = [i for i in ledger.drain_intents() if i.instrument_id.symbol == "AAA"]
    assert buys and buys[0].quantity == pytest.approx(200_000 * 0.98 / 2 / 100, abs=1)
