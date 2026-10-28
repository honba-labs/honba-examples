"""tests/unit/test_backtest_parity.py: Phase 8 backtest parity (golden vectors).

Asserts the Python backtest (``honba_examples.backtest.run_portfolio_backtest``) returns
match a pinned golden vector within ``1e-6``. The golden vector represents the compiled
Rust backtest (``honba-ya-backtest``) output: until the Rust golden vector is wired into CI,
this snapshot makes ``make test-parity`` executable and fully offline.

Run::

    pytest tests/unit/test_backtest_parity.py -q
    make test-parity
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from honba.domain.instrument import InstrumentId
from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma

from honba_examples.synthetic import trending_bars

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "snapshots" / "backtest_parity_golden.json"
TOLERANCE = 1e-6


class _SmaCross(Strategy):
    """Inline SMA crossover mirroring the golden-vector run."""

    name = "sma_cross"
    warmup_bars = 30

    def __init__(self, fast: int = 10, slow: int = 30, symbol: str = "RELIANCE") -> None:
        super().__init__()
        self.fast = Sma(fast)
        self.slow = Sma(slow)
        self.symbol = symbol
        self.iid = InstrumentId(symbol, exchange="NSE")

    def on_bar(self, bar, ctx=None) -> None:
        if bar.instrument_id != self.iid:
            return
        fv = self.fast.update(bar.close)
        sv = self.slow.update(bar.close)
        if fv is None or sv is None:
            return
        position = self.position(self.iid)
        if fv > sv and position <= 0:
            self.buy(self.iid, 1)
        elif fv < sv and position > 0:
            self.sell(self.iid, position)


def _load_backtest_module():

    spec = importlib.util.spec_from_file_location(
        "backtest_parity_probe",
        ROOT / "src" / "honba_examples" / "backtest.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules so dataclass field-annotation resolution in the loaded
    # module finds its own module (mirrors tests/unit/test_16_*.py).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def backtest_module():
    return _load_backtest_module()


def test_python_backtest_matches_golden_within_tolerance(backtest_module):
    golden = json.loads(GOLDEN.read_text())
    expected = golden["expected"]

    start = dt.date.fromisoformat(golden["test_start"])
    end = dt.date.fromisoformat(golden["test_end"])
    bars = trending_bars(
        golden["symbol"],
        golden["bars"],
        start=start,
        base=2500.0,
        step=0.5,
        exchange=golden["exchange"],
    )

    strat = _SmaCross(fast=golden["fast"], slow=golden["slow"], symbol=golden["symbol"])
    run = backtest_module.run_portfolio_backtest(
        strat,
        bars,
        test_start=start,
        test_end=end,
        capital_minor=int(golden["capital_inr"] * 100),
    )
    m = run.metrics()
    mm = run.major_metrics()

    actual = {
        "final_equity": mm["final_equity"],
        "total_return": mm["final_equity"] - golden["capital_inr"],
        "max_drawdown_pct": m["max_drawdown_pct"],
        "sharpe": m["sharpe"],
        "n_fills": m["n_fills"],
    }

    for key in ("final_equity", "total_return", "max_drawdown_pct", "sharpe"):
        assert key in actual, key
        assert abs(actual[key] - expected[key]) <= TOLERANCE, (
            f"{key}: {actual[key]} != {expected[key]} (tol {TOLERANCE})"
        )
    assert actual["n_fills"] == expected["n_fills"]
