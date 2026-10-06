"""backtesting/03_latency_modeling: next-open vs next-close fill models.

Honba supports multiple fill models: ``next_open`` (daily, next session's open),
``next_close`` (next session's close), and ``bar_close`` (current bar close,
vectorized backtests). This example runs the same strategy under each model
and compares fill prices and equity curves.

Run::

    python backtesting/03_latency_modeling.py
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
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma
from honba.strategies.testing import replay
from honba_examples.jsonable import jsonable
from tests.synthetic import bar as synth_bar, weekdays as weekdays_func

__all__ = ["run", "main"]


class SmaLatency(Strategy):
    """SMA crossover for fill-model comparison."""

    name: str = "sma_latency"
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


def _run_model(bars_list: list[Bar], fill_delay: int) -> dict[str, Any]:
    """Run replay with a specific fill_delay (0=bar_close, 1=next_open, 2=next_close etc)."""
    strat = SmaLatency()
    result = replay(strat, bars_list, fill_delay=fill_delay)
    return {
        "fill_delay": fill_delay,
        "fills": len(result.fills),
        "first_fill_price": result.fills[0].price if result.fills else None,
        "last_fill_price": result.fills[-1].price if result.fills else None,
    }


def run(
    bars: int = 60,
    fast: int = 10,
    slow: int = 30,
) -> dict[str, Any]:
    start = dt.date(2026, 1, 1)
    bars_list = [synth_bar("RELIANCE", d, 2500.0 + i * 0.5) for i, d in enumerate(weekdays_func(start, bars))]

    # Three models: 0 = bar close (vectorized), 1 = next open (Honba default), 2 = next close
    models = {
        "bar_close": _run_model(bars_list, fill_delay=0),
        "next_open": _run_model(bars_list, fill_delay=1),
        "next_close": _run_model(bars_list, fill_delay=2),
    }

    return {
        "config": {"fast": fast, "slow": slow, "bars": bars},
        "models": models,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bars", type=int, default=60)
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(bars=args.bars, fast=args.fast, slow=args.slow)

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())