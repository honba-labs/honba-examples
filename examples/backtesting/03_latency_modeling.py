"""backtesting/03_latency_modeling: bar-close vs next-open vs later-open fill models.

Honba has two fill models for bar data: ``bar_close`` (the decision bar's close, the single-price
conformance model) and ``next_open`` (daily, next session's open, simulated by
``honba.backtest.simulated.NextOpenExecution``). This example runs the same strategy under both
and under a third stress case, ``open_after_2_bars`` (``fill_delay=2``: the open two bars after the
signal), and compares fill prices. The engine has no next-close model.

Run::

    python backtesting/03_latency_modeling.py
    python backtesting/03_latency_modeling.py --fast 5 --slow 20

Pitfalls::

    - Label versus engine: replay's fill_delay counts bars. 0 fills at the signal bar's close, and 1
      or 2 fill at the open of the bar 1 or 2 later. Only bar_close and next_open match Honba's
      fill models; open_after_2_bars is a latency stress test, not an engine model.
    - Check that a knob reaches the code: --fast and --slow used to be echoed into config while the
      strategy kept its 10/30 defaults. Vary a parameter and confirm the output moves.
    - Lookahead is the gap: bar_close fills at a close that is only known after the session ends.
      The price difference between the bar_close and next_open rows is the move you would otherwise
      book as profit.
    - Understated gaps: the synthetic bars have open equal to close and rise 0.5 per bar, so a
      one-bar delay moves the fill by 0.5. Real overnight gaps and opening-auction slippage are far
      larger and are not simulated.
    - Compare fill prices, not P&L: each model produces one or two fills on a monotone series, which
      is too few to say anything about performance.
"""

from __future__ import annotations

import datetime as dt
import sys
from typing import Any

from honba.domain.bar import Bar
from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma
from honba.strategies.testing import replay

from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.synthetic import bar as synth_bar
from honba_examples.synthetic import weekdays as weekdays_func

__all__ = ["main", "run"]


class SmaLatency(Strategy):
    """SMA crossover for fill-model comparison."""

    name = "sma_latency"
    warmup_bars = 30

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


def _run_model(bars_list: list[Bar], fill_delay: int, fast: int, slow: int) -> dict[str, Any]:
    """Run replay with a specific fill_delay (0=bar_close, 1=next_open, 2=open two bars later)."""
    strat = SmaLatency(fast=fast, slow=slow)
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
    bars_list = [
        synth_bar("RELIANCE", d, 2500.0 + i * 0.5) for i, d in enumerate(weekdays_func(start, bars))
    ]

    # 0 = bar close (conformance model), 1 = next open (Honba's daily model), 2 = the open two bars
    # later (a latency stress test; the engine has no next-close model)
    models = {
        "bar_close": _run_model(bars_list, 0, fast, slow),
        "next_open": _run_model(bars_list, 1, fast, slow),
        "open_after_2_bars": _run_model(bars_list, 2, fast, slow),
    }

    return {
        "config": {"fast": fast, "slow": slow, "bars": bars},
        "models": models,
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--bars", type=int, default=60)
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(bars=args.bars, fast=args.fast, slow=args.slow)

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
