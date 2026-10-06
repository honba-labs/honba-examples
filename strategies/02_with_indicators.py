"""strategies/02_with_indicators: composite indicators + position sizing + risk rules.

Builds on 01 by adding:
- RSI filter (only buy when RSI < 70, sell when RSI > 30)
- ATR-based position sizing (risk 1% of equity per trade)
- ATR trailing stop on exit

Runs on synthetic bars with ``honba.strategies.testing.replay``.

Run::

    python strategies/02_with_indicators.py
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
from honba.strategies.indicators import Rsi, Atr, Sma
from honba.strategies.testing import replay
from honba_examples.jsonable import jsonable
from tests.synthetic import bar, weekdays as weekdays_func

__all__ = ["run", "main"]


class CompositeSmaRsi(Strategy):
    """SMA crossover with RSI filter and ATR trailing stop."""

    name: str = "composite_sma_rsi"
    warmup_bars: int = 30

    def __init__(
        self,
        fast: int = 10,
        slow: int = 30,
        rsi_period: int = 14,
        atr_period: int = 14,
        risk_pct: float = 0.01,
    ) -> None:
        super().__init__()
        self.fast_sma = Sma(fast)
        self.slow_sma = Sma(slow)
        self.rsi = Rsi(rsi_period)
        self.atr = Atr(atr_period)
        self.risk_pct = risk_pct
        self._trailing: dict[str, float] = {}

    def on_bar(self, bar) -> None:
        close = bar.close
        fast_val = self.fast_sma.update(close)
        slow_val = self.slow_sma.update(close)

        rsi_val = self.rsi.update(close)
        atr_val = self.atr.update(bar.high, bar.low, bar.close)

        if fast_val is None or slow_val is None or rsi_val is None or atr_val is None:
            return

        position = self.position(bar.instrument_id)

        # Entry: fast > slow and RSI not overbought
        if fast_val > slow_val and position <= 0 and rsi_val < 70:
            # ATR-based position sizing: risk = equity * risk_pct
            equity = 100_000.0  # Simplified - no live equity tracking
            risk_amount = equity * self.risk_pct
            qty = max(1, int(risk_amount / (atr_val * 2)))  # 2 ATR stop
            self.buy(bar.instrument_id, qty)
            self._trailing[bar.instrument_id.symbol] = close - 2 * atr_val

        # Exit: fast < slow or trailing stop hit
        elif fast_val < slow_val and position > 0:
            self.sell(bar.instrument_id, position)

        # Trailing stop update
        symbol = bar.instrument_id.symbol
        if symbol in self._trailing and position > 0:
            trail = close - 2 * atr_val
            if trail > self._trailing[symbol]:
                self._trailing[symbol] = trail
            elif close < self._trailing[symbol]:
                self.sell(bar.instrument_id, position)


def run(
    bars: int = 100,
    fast: int = 10,
    slow: int = 30,
    rsi_period: int = 14,
    atr_period: int = 14,
    risk_pct: float = 0.01,
) -> dict[str, Any]:
    start = dt.date(2026, 1, 1)
    bars_list = [bar("RELIANCE", d, 2500.0 + i * 0.5) for i, d in enumerate(weekdays_func(start, bars))]

    strat = CompositeSmaRsi(
        fast=fast, slow=slow, rsi_period=rsi_period, atr_period=atr_period, risk_pct=risk_pct
    )
    result = replay(strat, bars_list)

    strategy_repr = {
        "name": strat.name,
        "warmup_bars": strat.warmup_bars,
        "params": {
            "fast": fast,
            "slow": slow,
            "rsi_period": rsi_period,
            "atr_period": atr_period,
            "risk_pct": risk_pct,
        },
    }

    return {
        "strategy": strategy_repr,
        "config": {
            "fast": fast,
            "slow": slow,
            "rsi_period": rsi_period,
            "atr_period": atr_period,
            "risk_pct": risk_pct,
            "bars": bars,
        },
        "result": jsonable(result),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bars", type=int, default=100)
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--rsi-period", type=int, default=14)
    parser.add_argument("--atr-period", type=int, default=14)
    parser.add_argument("--risk-pct", type=float, default=0.01)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(
        bars=args.bars,
        fast=args.fast,
        slow=args.slow,
        rsi_period=args.rsi_period,
        atr_period=args.atr_period,
        risk_pct=args.risk_pct,
    )

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())