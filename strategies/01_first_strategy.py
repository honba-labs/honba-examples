"""strategies/01_first_strategy: a minimal SMA crossover strategy.

Subclass ``honba.strategies.base.Strategy``, set ``name`` and ``warmup_bars``, and
implement ``on_bar``. The strategy emits market orders through ``self.buy``/
``self.sell`` (or ``self.submit`` for limit/stop variants). The runner binds a
context, calls ``on_start`` → streams bars → ``on_stop``.

This example runs a fast SMA(10) vs slow SMA(30) crossover on a synthetic
100-bar series (see ``tests/synthetic.py``) using the testing harness
``honba.strategies.testing.replay`` — no adapter, no Parquet store, no network.

Run::

    python strategies/01_first_strategy.py
    python strategies/01_first_strategy.py --out first_strategy.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:  # plain checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma
from honba.strategies.testing import replay

from honba_examples.jsonable import jsonable
from tests.synthetic import bar
from tests.synthetic import weekdays as weekdays_func

__all__ = ["main", "run"]


class SmaCrossover(Strategy):
    """Fast SMA crossing above/below slow SMA → buy/sell one unit."""

    name: str = "sma_crossover"
    warmup_bars: int = 30

    def __init__(self, fast: int = 10, slow: int = 30, quantity: float = 1.0) -> None:
        super().__init__()
        self.fast = Sma(fast)
        self.slow = Sma(slow)
        self.quantity = quantity

    def on_bar(self, bar) -> None:
        fast_val = self.fast.update(bar.close)
        slow_val = self.slow.update(bar.close)

        if fast_val is None or slow_val is None:
            return  # still warming up

        position = self.position(bar.instrument_id)
        if fast_val > slow_val and position <= 0:
            self.buy(bar.instrument_id, self.quantity)
        elif fast_val < slow_val and position >= 0:
            self.sell(bar.instrument_id, self.quantity)


def run(
    bars: int = 100,
    fast: int = 10,
    slow: int = 30,
    quantity: float = 1.0,
) -> dict[str, Any]:
    """Run the strategy on synthetic bars and return a JSON-serializable report."""
    import datetime as dt

    start = dt.date(2026, 1, 1)
    bars_list = [bar("RELIANCE", d, 2500.0 + i * 0.5) for i, d in enumerate(weekdays_func(start, bars))]

    strat = SmaCrossover(fast=fast, slow=slow, quantity=quantity)
    result = replay(strat, bars_list)

    # Strategy is not a dataclass; build a manual representation
    strategy_repr = {
        "name": strat.name,
        "warmup_bars": strat.warmup_bars,
        "params": {
            "fast": fast,
            "slow": slow,
            "quantity": quantity,
        },
    }

    return {
        "strategy": strategy_repr,
        "config": {
            "fast": fast,
            "slow": slow,
            "quantity": quantity,
            "bars": bars,
        },
        "result": jsonable(result),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bars", type=int, default=100, help="number of synthetic bars")
    parser.add_argument("--fast", type=int, default=10, help="fast SMA period")
    parser.add_argument("--slow", type=int, default=30, help="slow SMA period")
    parser.add_argument("--quantity", type=float, default=1.0, help="position size per signal")
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON here")
    args = parser.parse_args(argv)

    result = run(
        bars=args.bars,
        fast=args.fast,
        slow=args.slow,
        quantity=args.quantity,
    )

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())