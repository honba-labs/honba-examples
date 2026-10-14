"""tests/unit/test_production_equity.py: paper-trading equity is marked to market every bar."""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import pytest
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderSide
from honba.strategies.testing import Trade

from honba_examples.metrics import fills_equity_curve
from honba_examples.synthetic import bar, weekdays

ROOT = Path(__file__).resolve().parents[2]
IID = InstrumentId("RELIANCE", "NSE")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "production" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_open_position_is_marked_at_each_bar_close() -> None:
    days = weekdays(dt.date(2026, 1, 5), 4)
    bars = [bar("RELIANCE", d, p) for d, p in zip(days, [100.0, 100.0, 110.0, 90.0])]
    fills = [Trade(IID, OrderSide.BUY, 2, 100.0, bars[1].ts)]
    curve = fills_equity_curve(1_000.0, fills, bars)
    # start, bar0 flat cash, bar1 buy 2 @100 then close 100, bar2 close 110, bar3 close 90
    assert curve == pytest.approx([1_000.0, 1_000.0, 1_000.0, 1_020.0, 980.0])


def test_paper_trading_final_equity_includes_the_open_position() -> None:
    out = _load("01_paper_trading").run()
    s = out["summary"]
    assert s["total_fills"] == 1  # the uptrend buys once and holds
    assert s["total_return"] > 0.0  # a held long in a rising series has unrealised gain
    assert s["final_equity"] == pytest.approx(out["equity_curve"][-1])
    assert len(out["equity_curve"]) == out["config"]["bars"] + 1


def test_multi_account_final_equity_includes_the_open_position() -> None:
    out = _load("03_multi_account").run()
    assert all(acc["fills"] == 1 and acc["total_return"] > 0.0 for acc in out["accounts"])


def test_missing_accounts_file_is_an_error_not_the_demo(tmp_path, capsys) -> None:
    mod = _load("03_multi_account")
    with pytest.raises(SystemExit) as exc:
        mod.main(["--accounts", str(tmp_path / "nope.yaml")])
    assert exc.value.code == 2
    assert "nope.yaml" in capsys.readouterr().err
