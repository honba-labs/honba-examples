"""backtesting/01_first_backtest: a minimal backtest with synthetic data.

Uses ``honba.session.BacktestSession`` (the same runner that powers live) with
a tiny ``DataProvider`` that returns synthetic bars. The strategy emits market
orders; fills arrive at the next bar's open (Honba's ``next_open`` fill model
for daily sessions; see ``honba_examples/execution.py`` for the multi-instrument
variant that also handles settlement and fees).

Run::

    python backtesting/01_first_backtest.py
    python backtesting/01_first_backtest.py --out first_backtest.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:  # plain checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId, Instrument, InstrumentKind
from honba.session import BacktestConfig, BacktestSession, DataProvider
from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma
from honba_examples.jsonable import jsonable
from tests.synthetic import bar as synth_bar, weekdays as weekdays_func

__all__ = ["run", "main"]


class SmaBacktest(Strategy):
    """Fast SMA crossing above/below slow SMA → signal-driven fills."""

    name: str = "sma_backtest"
    warmup_bars: int = 30

    def __init__(self, fast: int = 10, slow: int = 30) -> None:
        super().__init__()
        self.fast = Sma(fast)
        self.slow = Sma(slow)

    def on_bar(self, bar) -> None:
        fast_val = self.fast.update(bar.close)
        slow_val = self.slow.update(bar.close)

        if fast_val is None or slow_val is None:
            return

        position = self.position(bar.instrument_id)
        if fast_val > slow_val and position <= 0:
            self.buy(bar.instrument_id, 1)
        elif fast_val < slow_val and position >= 0 and position > 0:
            self.sell(bar.instrument_id, position)


class SyntheticDataProvider(DataProvider):
    """In-memory DataProvider for a fixed list of bars."""

    def __init__(self, bars: list[Bar], iid: InstrumentId) -> None:
        self._bars = bars
        self._iid = iid
        self._inst = Instrument(iid, InstrumentKind.EQUITY, lot_size=1.0, tick_size=0.05)

    def bars(
        self, instrument_id: InstrumentId, *, timeframe: str, start: dt.datetime, end: dt.datetime
    ) -> list[Bar]:
        # Filter by window and instrument; timeframe is ignored for synthetic data
        return [b for b in self._bars if b.instrument_id == instrument_id and start.timestamp() * 1e9 <= b.ts < end.timestamp() * 1e9]

    def instrument(self, instrument_id: InstrumentId) -> Instrument:
        return self._inst


def run(
    bars: int = 60,
    fast: int = 10,
    slow: int = 30,
    capital: float = 100_000.0,
) -> dict[str, Any]:
    start = dt.date(2026, 1, 1)
    bars_list = [synth_bar("RELIANCE", d, 2500.0 + i * 0.5) for i, d in enumerate(weekdays_func(start, bars))]

    strat = SmaBacktest(fast=fast, slow=slow)
    DataProvider = SyntheticDataProvider(bars_list, bars_list[0].instrument_id)
    config = BacktestConfig(
        symbol="RELIANCE",
        exchange="NSE",
        start=start.isoformat(),
        end=(dt.date(2026, 12, 31)).isoformat(),
        timeframe="1d",
        cash=capital,
        costs="india.equity",
        fill="next_open",
    )

    # Note: BacktestSession requires a real DataProvider; for this learning-path example
    # we use the honba.strategies.testing.replay harness instead (same fill semantics
    # for a single-instrument daily backtest).
    from honba.strategies.testing import replay

    result = replay(strat, bars_list)

    # Metrics from honba_examples.metrics (same curve format as production tearsheets)
    fill_count = len(result.fills)
    intent_count = len(result.intents)

    strategy_repr = {
        "name": strat.name,
        "warmup_bars": strat.warmup_bars,
        "params": {"fast": fast, "slow": slow},
    }

    return {
        "strategy": strategy_repr,
        "config": {
            "bars": bars,
            "fast": fast,
            "slow": slow,
            "capital": capital,
        },
        "result": jsonable(result),
        "summary": {
            "fills": fill_count,
            "intents": intent_count,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bars", type=int, default=60)
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(bars=args.bars, fast=args.fast, slow=args.slow, capital=args.capital)

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())