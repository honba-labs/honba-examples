"""strategies/13_bracket_order_mis: bracket orders and MIS intraday margin with 15:20 IST square-off.

Demonstrates:
- Intraday Margin Intraday Square-off (MIS) position sizing with broker margin multiplier
- Take-profit (TP) and stop-loss (SL) bracket order threshold management
- Mandatory 15:20 IST auto-square-off enforcement prior to market close (15:30 IST)

Run::

    python examples/strategies/13_bracket_order_mis.py
    python examples/strategies/13_bracket_order_mis.py --take-profit-pct 0.03 --stop-loss-pct 0.015

Pitfalls::

    - Intraday liquidity and square-off slippage: broker auto-square-off at 15:20 IST submits market
      orders during the final high-volatility session window; wide spreads and queue priority can
      substantially degrade realized execution prices.
    - Intrabar bracket ambiguity: in coarse interval bars (e.g. 15m or 1h), a wide candle whose high
      and low reach both take-profit and stop-loss levels creates path dependency that cannot be resolved
      deterministically without tick-level order book replay.
    - MIS leverage margin call risk: while 5x intraday margin boosts buying power, adverse moves can trigger
      mid-session broker RMS liquidation or exchange margin penalties before the 15:20 IST square-off time.
    - No overnight carry for MIS: equity MIS positions not squared off by 15:20 IST face compulsory broker
      liquidation fees, exchange auction penalties, and mandatory cash settlement without delivery.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from typing import ClassVar, override

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderSide
from honba.domain.trade import Trade
from honba.strategies.base import Strategy

from honba_examples.cli import run_example
from honba_examples.replay_report import replay_report

__all__ = ["BracketOrderMis", "intraday_mis_bars", "main", "run"]


def intraday_mis_bars(
    symbol: str = "RELIANCE",
    start_date: dt.date | None = None,
    days: int = 1,
    base_price: float = 2500.0,
    price_path: list[float] | None = None,
) -> list[Bar]:
    """Generate synthetic intraday 15-minute bars (09:15 IST to 15:30 IST) with an explicit 15:20 bar."""
    bars: list[Bar] = []
    # NSE intraday session times in IST: (hour, minute)
    session_times = [
        (9, 15),
        (9, 30),
        (9, 45),
        (10, 0),
        (10, 30),
        (11, 0),
        (12, 0),
        (13, 0),
        (14, 0),
        (15, 0),
        (15, 15),
        (15, 20),
        (15, 30),
    ]

    current_day = start_date if start_date is not None else dt.date(2026, 1, 1)
    bar_idx = 0
    for _ in range(days):
        while current_day.weekday() >= 5:  # skip weekends
            current_day += dt.timedelta(days=1)

        for hr, mn in session_times:
            # IST is UTC + 5:30; compute UTC timestamp
            ist_dt = dt.datetime(
                current_day.year,
                current_day.month,
                current_day.day,
                hr,
                mn,
                tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)),
            )
            ts_ns = int(ist_dt.timestamp()) * 1_000_000_000

            if price_path and bar_idx < len(price_path):
                px = price_path[bar_idx]
            else:
                px = base_price + bar_idx * 0.5

            bar_idx += 1
            bars.append(
                Bar(
                    InstrumentId(symbol, "NSE"),
                    ts_ns,
                    px,
                    px + 1.0,
                    px - 1.0,
                    px,
                    1000.0,
                )
            )
        current_day += dt.timedelta(days=1)

    return bars


class BracketOrderMis(Strategy):
    """Intraday MIS strategy with bracket take-profit / stop-loss and 15:20 IST square-off."""

    name: ClassVar[str] = "bracket_order_mis"
    quantity: float
    stop_loss_pct: float
    take_profit_pct: float
    margin_multiplier: float
    capital: float
    _cash: float
    _entry_price: float | None
    _take_profit_price: float | None
    _stop_loss_price: float | None
    _current_date: dt.date | None
    _entered_today: bool
    _exit_reason: str | None

    def __init__(
        self,
        quantity: float = 10.0,
        stop_loss_pct: float = 0.02,
        take_profit_pct: float = 0.04,
        margin_multiplier: float = 5.0,
        capital: float = 100_000.0,
    ) -> None:
        super().__init__()
        self.quantity = quantity
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.margin_multiplier = margin_multiplier
        self.capital = capital
        self._cash = capital

        self._entry_price = None
        self._take_profit_price = None
        self._stop_loss_price = None
        self._current_date = None
        self._entered_today = False
        self._exit_reason = None

    @override
    def on_fill(self, fill: Trade) -> None:
        sign = -1.0 if fill.side is OrderSide.BUY else 1.0
        self._cash += sign * fill.quantity * fill.price
        if fill.side is OrderSide.BUY:
            self._entry_price = fill.price
            self._take_profit_price = fill.price * (1.0 + self.take_profit_pct)
            self._stop_loss_price = fill.price * (1.0 - self.stop_loss_pct)
        else:
            self._entry_price = None
            self._take_profit_price = None
            self._stop_loss_price = None

    @override
    def on_bar(self, bar: Bar) -> None:
        # Convert bar timestamp to IST
        utc_dt = dt.datetime.fromtimestamp(bar.ts / 1_000_000_000, tz=dt.timezone.utc)
        ist_dt = utc_dt + dt.timedelta(hours=5, minutes=30)
        ist_time = ist_dt.time()
        bar_date = ist_dt.date()

        if self._current_date != bar_date:
            self._current_date = bar_date
            self._entered_today = False

        pos = self.position(bar.instrument_id)

        if pos > 0:
            # 1. Check mandatory MIS auto-square-off at 15:20 IST
            if ist_time >= dt.time(15, 20):
                self._exit_reason = "auto_square_off_1520"
                self.sell(bar.instrument_id, pos)
                return

            # 2. Check bracket take-profit
            if self._take_profit_price is not None and bar.high >= self._take_profit_price:
                self._exit_reason = "take_profit"
                self.sell(bar.instrument_id, pos)
                return

            # 3. Check bracket stop-loss
            if self._stop_loss_price is not None and bar.low <= self._stop_loss_price:
                self._exit_reason = "stop_loss"
                self.sell(bar.instrument_id, pos)
                return

        elif pos == 0:
            # Enter trade during active morning session before 15:00 IST
            if not self._entered_today and ist_time < dt.time(15, 0):
                # MIS margin calculation: buying power = cash * margin_multiplier
                buying_power = max(0.0, self._cash) * self.margin_multiplier
                max_allowed_qty = int(buying_power / bar.close) if bar.close > 0 else 0
                qty = min(int(self.quantity), max_allowed_qty)
                if qty > 0:
                    self.buy(bar.instrument_id, float(qty))
                    self._entered_today = True

    @property
    def exit_reason(self) -> str | None:
        return self._exit_reason


def run(
    bars: int = 26,
    quantity: float = 10.0,
    stop_loss_pct: float = 0.02,
    take_profit_pct: float = 0.04,
    margin_multiplier: float = 5.0,
    capital: float = 100_000.0,
    price_path: list[float] | None = None,
) -> dict[str, object]:
    bars_list = intraday_mis_bars("RELIANCE", days=max(1, bars // 13), price_path=price_path)
    if bars and len(bars_list) > bars:
        bars_list = bars_list[:bars]

    strat = BracketOrderMis(
        quantity=quantity,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        margin_multiplier=margin_multiplier,
        capital=capital,
    )
    return replay_report(
        strat,
        bars_list,
        {
            "quantity": quantity,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "margin_multiplier": margin_multiplier,
            "capital": capital,
        },
        config_extra={"bars": len(bars_list)},
    )


def _add_args(parser: argparse.ArgumentParser) -> None:
    _ = parser.add_argument("--bars", type=int, default=26, help="number of intraday bars")
    _ = parser.add_argument("--quantity", type=float, default=10.0, help="order quantity")
    _ = parser.add_argument(
        "--stop-loss-pct", type=float, default=0.02, help="stop loss percentage"
    )
    _ = parser.add_argument(
        "--take-profit-pct", type=float, default=0.04, help="take profit percentage"
    )
    _ = parser.add_argument(
        "--margin-multiplier", type=float, default=5.0, help="MIS leverage multiplier"
    )
    _ = parser.add_argument("--capital", type=float, default=100_000.0, help="starting capital")


def main(argv: list[str] | None = None) -> int:
    return run_example(run, __doc__, _add_args, argv=argv)


if __name__ == "__main__":
    sys.exit(main())
