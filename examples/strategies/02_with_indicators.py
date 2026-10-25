"""strategies/02_with_indicators: composite indicators + position sizing + risk rules.

Builds on 01 by adding:
- SMA or EMA trend filter (configurable with ``use_ema``)
- RSI filter (only buy when RSI < 70; exits come from the MA cross or the stop, not from RSI)
- ATR-based position sizing (risk 1% of equity per trade)
- ATR trailing stop on exit

Runs on deterministic sine-wave bars (``oscillating_bars``) with ``honba.strategies.testing.replay``.

Run::

    python examples/strategies/02_with_indicators.py
    python examples/strategies/02_with_indicators.py --use-ema

Pitfalls::

    - The default data is a sine wave on purpose: on a monotone ramp RSI is 100, the RSI < 70 entry
      never fires and the run is silent. A filter you never see firing proves nothing; count
      signals with and without each filter before believing it helps.
    - Sizing sees a fixed account and no cap: equity is hard-coded to 100000, so risk_pct is a
      percentage of a constant, and nothing limits size by cash. The default run buys about 59
      shares near 2500, roughly 147000 INR of notional against 100000 of equity.
    - Stop at the close: the trailing stop is compared with closes, never intrabar, and a replay
      fill lands on that same close. A gap through the stop fills worse live, and the close that
      triggers the exit is not tradable.
    - Degrees of freedom: fast, slow, rsi_period, atr_period and risk_pct plus the fixed 70 and 2 x
      ATR constants. Each knob is another way to fit one history; validate on data you did not tune
      on (see backtesting/04_walk_forward).
    - Moving average lag: while EMA reacts faster than SMA to recent price changes, it can also
      produce more whipsaws and false breakout signals in choppy sideways markets.
    - Defence shown: on_bar returns until all four indicators (two MA, Rsi, Atr) have values.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from honba.strategies.base import Strategy
from honba.strategies.indicators import Atr, Ema, Rsi, Sma

from honba_examples.cli import run_example
from honba_examples.replay_report import replay_report
from honba_examples.synthetic import oscillating_bars

__all__ = ["main", "run"]


class CompositeSmaRsi(Strategy):
    """SMA/EMA crossover with RSI filter and ATR trailing stop."""

    name = "composite_sma_rsi"
    warmup_bars = 30

    def __init__(
        self,
        fast: int = 10,
        slow: int = 30,
        rsi_period: int = 14,
        atr_period: int = 14,
        risk_pct: float = 0.01,
        use_ema: bool = False,
    ) -> None:
        super().__init__()
        self.use_ema = use_ema
        self.fast_ma = Ema(fast) if use_ema else Sma(fast)
        self.slow_ma = Ema(slow) if use_ema else Sma(slow)
        self.rsi = Rsi(rsi_period)
        self.atr = Atr(atr_period)
        self.risk_pct = risk_pct
        self._trailing: dict[str, float] = {}

    def on_bar(self, bar) -> None:
        close = bar.close
        fast_val = self.fast_ma.update(close)
        slow_val = self.slow_ma.update(close)

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
    use_ema: bool = False,
) -> dict[str, Any]:
    bars_list = oscillating_bars("RELIANCE", bars)

    strat = CompositeSmaRsi(
        fast=fast,
        slow=slow,
        rsi_period=rsi_period,
        atr_period=atr_period,
        risk_pct=risk_pct,
        use_ema=use_ema,
    )
    return replay_report(
        strat,
        bars_list,
        {
            "fast": fast,
            "slow": slow,
            "rsi_period": rsi_period,
            "atr_period": atr_period,
            "risk_pct": risk_pct,
            "use_ema": use_ema,
        },
        config_extra={"bars": bars},
    )


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bars", type=int, default=100)
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--rsi-period", type=int, default=14)
    parser.add_argument("--atr-period", type=int, default=14)
    parser.add_argument("--risk-pct", type=float, default=0.01)
    parser.add_argument("--use-ema", action="store_true", help="use EMA instead of SMA")


def main(argv: list[str] | None = None) -> int:
    return run_example(run, __doc__, _add_args, argv=argv)


if __name__ == "__main__":
    sys.exit(main())
