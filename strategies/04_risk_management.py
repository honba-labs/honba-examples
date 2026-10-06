"""strategies/04_risk_management: max drawdown, daily loss limit, position limits.

Risk controls enforced inside the strategy (not the engine):
- Max portfolio drawdown from peak equity
- Max daily loss
- Max position size per instrument
- Max sector exposure (simplified: single instrument)

Run::

    python strategies/04_risk_management.py
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

from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma
from honba.strategies.testing import replay
from honba_examples.jsonable import jsonable
from tests.synthetic import bar, weekdays as weekdays_func

__all__ = ["run", "main"]


class RiskManagedSma(Strategy):
    """SMA crossover with risk guards."""

    name: str = "risk_managed_sma"
    warmup_bars: int = 30

    def __init__(
        self,
        fast: int = 10,
        slow: int = 30,
        max_drawdown_pct: float = 0.10,
        max_daily_loss_pct: float = 0.02,
        max_position_pct: float = 0.20,
    ) -> None:
        super().__init__()
        self.fast_sma = Sma(fast)
        self.slow_sma = Sma(slow)
        self.max_drawdown_pct = max_drawdown_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_position_pct = max_position_pct
        self._peak_equity = 100_000.0
        self._day_start_equity = 100_000.0
        self._current_day = None

    def on_bar(self, bar) -> None:
        close = bar.close
        fast_val = self.fast_sma.update(close)
        slow_val = self.slow_sma.update(close)

        if fast_val is None or slow_val is None:
            return

        # Track equity and daily reset
        equity = self._estimate_equity(close)
        if self._current_day != bar.ts:
            self._current_day = bar.ts
            self._day_start_equity = equity
        if equity > self._peak_equity:
            self._peak_equity = equity

        # Risk checks
        drawdown = (self._peak_equity - equity) / self._peak_equity
        daily_loss = (self._day_start_equity - equity) / self._day_start_equity
        position = self.position(bar.instrument_id)
        position_pct = abs(position * close) / equity if equity > 0 else 0

        if drawdown >= self.max_drawdown_pct:
            self._flatten_all()
            return
        if daily_loss >= self.max_daily_loss_pct:
            self._flatten_all()
            return
        if position_pct >= self.max_position_pct:
            return  # don't add to position

        # Normal signals
        if fast_val > slow_val and position <= 0:
            qty = self._size(close, equity)
            if qty > 0:
                self.buy(bar.instrument_id, qty)
        elif fast_val < slow_val and position > 0:
            self.sell(bar.instrument_id, position)

    def _estimate_equity(self, price: float) -> float:
        """Rough equity estimate from cash + position value."""
        cash = 100_000.0  # Simplified
        for iid, qty in self.ctx.positions().items():
            cash += qty * price
        return cash

    def _size(self, price: float, equity: float) -> int:
        max_qty = int(equity * self.max_position_pct / price)
        return max(1, max_qty)

    def _flatten_all(self) -> None:
        for iid, qty in self.ctx.positions().items():
            if qty > 0:
                self.sell(iid, qty)


def run(
    bars: int = 100,
    fast: int = 10,
    slow: int = 30,
    max_drawdown_pct: float = 0.10,
    max_daily_loss_pct: float = 0.02,
    max_position_pct: float = 0.20,
) -> dict[str, Any]:
    start = dt.date(2026, 1, 1)
    bars_list = [bar("RELIANCE", d, 2500.0 + i * 0.5) for i, d in enumerate(weekdays_func(start, bars))]

    strat = RiskManagedSma(
        fast=fast,
        slow=slow,
        max_drawdown_pct=max_drawdown_pct,
        max_daily_loss_pct=max_daily_loss_pct,
        max_position_pct=max_position_pct,
    )
    result = replay(strat, bars_list)

    strategy_repr = {
        "name": strat.name,
        "warmup_bars": strat.warmup_bars,
        "params": {
            "fast": fast,
            "slow": slow,
            "max_drawdown_pct": max_drawdown_pct,
            "max_daily_loss_pct": max_daily_loss_pct,
            "max_position_pct": max_position_pct,
        },
    }

    return {
        "strategy": strategy_repr,
        "config": {
            "fast": fast,
            "slow": slow,
            "max_drawdown_pct": max_drawdown_pct,
            "max_daily_loss_pct": max_daily_loss_pct,
            "max_position_pct": max_position_pct,
            "bars": bars,
        },
        "result": jsonable(result),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bars", type=int, default=100)
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--max-drawdown-pct", type=float, default=0.10)
    parser.add_argument("--max-daily-loss-pct", type=float, default=0.02)
    parser.add_argument("--max-position-pct", type=float, default=0.20)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(
        bars=args.bars,
        fast=args.fast,
        slow=args.slow,
        max_drawdown_pct=args.max_drawdown_pct,
        max_daily_loss_pct=args.max_daily_loss_pct,
        max_position_pct=args.max_position_pct,
    )

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())