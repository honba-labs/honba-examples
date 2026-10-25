"""strategies/16_dynamic_position_sizing: dynamic position sizing models.

Demonstrates:
- Fixed lot size (static quantity baseline)
- Fixed fractional sizing (risk 1% of total equity per trade)
- Volatility-targeted sizing (scale inversely to ATR risk distance)
- Systematic comparison across identical price series

Run::

    python examples/strategies/16_dynamic_position_sizing.py --mode fixed_lot
    python examples/strategies/16_dynamic_position_sizing.py --mode fixed_fractional
    python examples/strategies/16_dynamic_position_sizing.py --mode volatility_targeted
    python examples/strategies/16_dynamic_position_sizing.py --compare

Pitfalls::

    - Volatility estimation lag: ATR is a lagging backward-looking metric; during sudden regime
      shifts or gap openings, ATR-based sizing significantly underestimates immediate tail risk.
    - Equity denominator reflexivity: dynamically sizing positions as a fraction of current equity
      can amplify drawdowns during losing streaks if margin requirements force unfavorable liquidations.
    - Odd-lot rounding error: on real exchanges, position sizes must be integer shares or multi-share
      lots; rounding small accounts' fractional allocations creates substantial tracking distortion.
    - Leverage inflation during low volatility: when ATR compresses to historical lows, volatility
      targeting drastically scales up nominal exposure, exposing the account to devastating black swan gaps.
"""

from __future__ import annotations

import argparse
import sys
from typing import ClassVar, cast, override

from honba.domain.bar import Bar
from honba.domain.order import OrderSide
from honba.domain.trade import Trade
from honba.strategies.base import Strategy
from honba.strategies.indicators import Atr, Sma

from honba_examples.cli import run_example
from honba_examples.replay_report import replay_report
from honba_examples.synthetic import get_bars_with_source, oscillating_bars

__all__ = ["DynamicPositionSizing", "compare_sizing_modes", "main", "run"]


class DynamicPositionSizing(Strategy):
    """Trend crossover with configurable dynamic position sizing models."""

    name: ClassVar[str] = "dynamic_position_sizing"
    warmup_bars: ClassVar[int] = 30
    mode: str
    fast: int
    slow: int
    fixed_quantity: float
    risk_pct: float
    atr_period: int
    atr_stop_mult: float
    capital: float
    _cash: float
    fast_sma: Sma
    slow_sma: Sma
    atr: Atr
    sizes_history: list[int]

    def __init__(
        self,
        mode: str = "fixed_fractional",  # "fixed_lot", "fixed_fractional", "volatility_targeted"
        fast: int = 10,
        slow: int = 30,
        fixed_quantity: float = 10.0,
        risk_pct: float = 0.01,
        atr_period: int = 14,
        atr_stop_mult: float = 2.0,
        capital: float = 100_000.0,
    ) -> None:
        super().__init__()
        self.mode = mode
        self.fast = fast
        self.slow = slow
        self.fixed_quantity = fixed_quantity
        self.risk_pct = risk_pct
        self.atr_period = atr_period
        self.atr_stop_mult = atr_stop_mult
        self.capital = capital
        self._cash = capital

        self.fast_sma = Sma(fast)
        self.slow_sma = Sma(slow)
        self.atr = Atr(atr_period)
        self.sizes_history = []

    @override
    def on_fill(self, fill: Trade) -> None:
        sign = -1.0 if fill.side is OrderSide.BUY else 1.0
        self._cash += sign * fill.quantity * fill.price

    @override
    def on_bar(self, bar: Bar) -> None:
        fast_val = self.fast_sma.update(bar.close)
        slow_val = self.slow_sma.update(bar.close)
        atr_val = self.atr.update(bar.high, bar.low, bar.close)

        if fast_val is None or slow_val is None or atr_val is None:
            return

        pos = self.position(bar.instrument_id)

        # Signal rules
        if fast_val > slow_val and pos <= 0:
            qty = self.compute_size(bar.close, atr_val)
            if qty > 0:
                self.sizes_history.append(qty)
                self.buy(bar.instrument_id, float(qty))
        elif fast_val < slow_val and pos > 0:
            self.sell(bar.instrument_id, pos)

    def _estimate_equity(self, price: float) -> float:
        equity = self._cash
        for qty in self.ctx.positions().values():
            equity += qty * price
        return equity

    def compute_size(self, price: float, atr_val: float) -> int:
        equity = self._estimate_equity(price)
        if equity <= 0 or price <= 0:
            return 0

        if self.mode == "fixed_lot":
            qty = int(self.fixed_quantity)
        elif self.mode == "fixed_fractional":
            # Risk 1% of equity with a 2% default stop distance
            stop_dist = max(1.0, price * 0.02)
            risk_budget = equity * self.risk_pct
            qty = int(risk_budget / stop_dist)
        elif self.mode == "volatility_targeted":
            # Risk 1% of equity targeting ATR stop distance
            stop_dist = max(1.0, atr_val * self.atr_stop_mult)
            risk_budget = equity * self.risk_pct
            qty = int(risk_budget / stop_dist)
        else:
            qty = int(self.fixed_quantity)

        # Cap at available cash (no leverage)
        affordable = int(max(0.0, self._cash) / price)
        return min(max(1, qty), affordable)


def compare_sizing_modes(
    bars: int = 150,
    capital: float = 100_000.0,
    risk_pct: float = 0.01,
) -> dict[str, dict[str, object]]:
    """Execute all three position sizing modes across the same bar series and compare."""
    modes = ["fixed_lot", "fixed_fractional", "volatility_targeted"]
    results: dict[str, dict[str, object]] = {}
    for m in modes:
        res = run(bars=bars, mode=m, capital=capital, risk_pct=risk_pct)
        res_dict = cast(dict[str, object], res.get("result", {}))
        fills_raw = res_dict.get("fills", [])
        fills = cast(list[dict[str, object]], fills_raw)
        sizes: list[float] = [
            float(str(f["quantity"])) for f in fills if str(f.get("side")) == "buy"
        ]
        avg_size = sum(sizes) / len(sizes) if sizes else 0.0
        final_cash = float(str(res_dict.get("cash", 0.0)))
        results[m] = {
            "trade_count": len(fills),
            "buy_orders": len(sizes),
            "avg_position_size": round(avg_size, 2),
            "final_cash": round(final_cash, 2),
        }
    return results


def run(
    bars: int = 150,
    mode: str = "fixed_fractional",
    fixed_quantity: float = 10.0,
    risk_pct: float = 0.01,
    atr_period: int = 14,
    atr_stop_mult: float = 2.0,
    capital: float = 100_000.0,
    compare: bool = False,
    use_synthetic: bool = True,
) -> dict[str, object]:
    if compare:
        comparison = compare_sizing_modes(bars=bars, capital=capital, risk_pct=risk_pct)
        return {
            "strategy": {"name": "dynamic_position_sizing", "mode": "comparison"},
            "config": {"bars": bars, "compare": True, "risk_pct": risk_pct},
            "result": comparison,
        }

    if use_synthetic:
        bars_list = oscillating_bars("NIFTY", bars, amplitude=80.0, drift=3.0)
        source = "synthetic"
    else:
        bars_list, source = get_bars_with_source("NIFTY", count=bars)

    strat = DynamicPositionSizing(
        mode=mode,
        fast=10,
        slow=30,
        fixed_quantity=fixed_quantity,
        risk_pct=risk_pct,
        atr_period=atr_period,
        atr_stop_mult=atr_stop_mult,
        capital=capital,
    )
    report = replay_report(
        strat,
        bars_list,
        {
            "mode": mode,
            "fixed_quantity": fixed_quantity,
            "risk_pct": risk_pct,
            "atr_period": atr_period,
            "atr_stop_mult": atr_stop_mult,
            "capital": capital,
            "source": source,
        },
        config_extra={"bars": len(bars_list)},
    )
    res_dict = report.get("result", {})
    if isinstance(res_dict, dict):
        res_dict["sizes_history"] = strat.sizes_history
    return report


def _add_args(parser: argparse.ArgumentParser) -> None:
    _ = parser.add_argument("--bars", type=int, default=150, help="number of bars")
    _ = parser.add_argument(
        "--mode",
        choices=["fixed_lot", "fixed_fractional", "volatility_targeted"],
        default="fixed_fractional",
        help="position sizing model",
    )
    _ = parser.add_argument(
        "--fixed-quantity", type=float, default=10.0, help="fixed quantity for fixed_lot mode"
    )
    _ = parser.add_argument(
        "--risk-pct", type=float, default=0.01, help="fraction of equity risked per trade"
    )
    _ = parser.add_argument("--atr-period", type=int, default=14, help="ATR period")
    _ = parser.add_argument("--atr-stop-mult", type=float, default=2.0, help="ATR stop multiplier")
    _ = parser.add_argument("--capital", type=float, default=100_000.0, help="initial capital")
    _ = parser.add_argument(
        "--compare", action="store_true", help="run all three sizing models and compare"
    )
    _ = parser.add_argument(
        "--use-lakehouse",
        action="store_false",
        dest="use_synthetic",
        help="attempt to load real historical bars from the lakehouse",
    )


def main(argv: list[str] | None = None) -> int:
    return run_example(run, __doc__, _add_args, argv=argv)


if __name__ == "__main__":
    sys.exit(main())
