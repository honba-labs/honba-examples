"""strategies/07_declarative_long_short: a Jesse-style long/short SMA crossover.

``honba.strategies.DeclarativeStrategy`` replaces the ``on_bar`` body with small rule hooks:

* ``should_long(bar)`` / ``should_short(bar)``: open a position when flat and idle;
* ``go_long(bar)`` / ``go_short(bar)``: return ``Entry(quantity, stop_loss, take_profit)``;
* ``should_exit(bar)``: flatten an open position at market.

The framework sends the market entry, and after the *entry fill* it submits the protective
orders sized to the filled quantity: for a long a stop-market sell at ``stop_loss`` and a
limit sell at ``take_profit``; for a short a stop-market buy and a limit buy. This example
crosses SMA(5) over SMA(20) on a synthetic rise-then-fall series, so it takes one long and
one short, and the report lists each entry with the levels it declared.

Known limitations (core ``DeclarativeStrategy``)
------------------------------------------------
* While protective orders rest, ``busy()`` is true, so ``should_exit`` is not consulted;
  exits then come from the stop or the target themselves.
* There is no automatic OCO cancel yet: the stop and the target are independent orders.
  The tiny harness below plays the exchange (it triggers resting orders from the bar range
  and cancels the sibling); the strategy class itself does not.

``honba.strategies.testing.replay`` is not used because it fills every intent, including
resting stops and limits, at once at the bar close. Here only market orders fill at the
close; stops and limits fill when a later bar's range touches their level.

Run::

    python strategies/07_declarative_long_short.py
    python strategies/07_declarative_long_short.py --stop-pct 0.02 --target-pct 0.05
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

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide, OrderType
from honba.entities.trade import Trade
from honba.strategies import DeclarativeStrategy, Entry
from honba.strategies.context import LedgerContext
from honba.strategies.indicators import Sma
from honba.strategies.testing import ReplayResult

from honba_examples.jsonable import jsonable
from tests.synthetic import bar
from tests.synthetic import weekdays as weekdays_func

__all__ = ["main", "run"]

SYMBOL = "RELIANCE"
IID = InstrumentId(SYMBOL, "NSE")


class LongShortCrossover(DeclarativeStrategy):
    """SMA(fast) crossing SMA(slow): long on a cross up, short on a cross down."""

    name = "declarative_long_short"

    def __init__(
        self,
        fast: int = 5,
        slow: int = 20,
        quantity: float = 10.0,
        stop_pct: float = 0.03,
        target_pct: float = 0.06,
    ) -> None:
        self.fast, self.slow = Sma(fast), Sma(slow)
        self.quantity, self.stop_pct, self.target_pct = quantity, stop_pct, target_pct
        self.entries: list[dict[str, Any]] = []
        self._prev_gap: float | None = None
        self._cross = 0  # +1 fast crossed above slow on this bar, -1 below, 0 neither

    def on_bar(self, bar: Bar) -> None:
        fast, slow = self.fast.update(bar.close), self.slow.update(bar.close)
        self._cross = 0
        if fast is not None and slow is not None:
            gap = fast - slow
            if self._prev_gap is not None and self._prev_gap <= 0 < gap:
                self._cross = 1
            elif self._prev_gap is not None and self._prev_gap >= 0 > gap:
                self._cross = -1
            self._prev_gap = gap
        super().on_bar(bar)  # the facade evaluates the rules below

    def should_long(self, bar: Bar) -> bool:
        return self._cross > 0

    def should_short(self, bar: Bar) -> bool:
        return self._cross < 0

    def go_long(self, bar: Bar) -> Entry:
        entry = Entry(
            self.quantity,
            stop_loss=bar.close * (1 - self.stop_pct),
            take_profit=bar.close * (1 + self.target_pct),
        )
        self._record("buy", bar, entry)
        return entry

    def go_short(self, bar: Bar) -> Entry:
        entry = Entry(
            self.quantity,
            stop_loss=bar.close * (1 + self.stop_pct),
            take_profit=bar.close * (1 - self.target_pct),
        )
        self._record("sell", bar, entry)
        return entry

    def _record(self, side: str, bar: Bar, entry: Entry) -> None:
        self.entries.append(
            {
                "side": side,
                "date": dt.datetime.fromtimestamp(bar.ts / 1e9, dt.timezone.utc).date(),
                "price": bar.close,
                "quantity": entry.quantity,
                "stop_loss": entry.stop_loss,
                "take_profit": entry.take_profit,
            }
        )


def _closes(bars: int) -> list[float]:
    """Flat, then a rally, then a sell-off: one golden cross and one death cross."""
    flat, up = bars // 4, bars // 4
    out = [100.0] * flat
    out += [100.0 + 1.5 * (i + 1) for i in range(up)]
    peak = out[-1]
    out += [peak - 2.0 * (i + 1) for i in range(bars - flat - up)]
    return out


def _triggered(intent: OrderIntent, b: Bar) -> float | None:
    """Fill price if the bar's range reaches a resting stop / limit, else ``None``."""
    sell = intent.side is OrderSide.SELL
    if intent.order_type is OrderType.STOP_MARKET:
        level = intent.trigger_price
        if level is None:
            return None
        hit = b.low <= level if sell else b.high >= level
    else:  # LIMIT
        level = intent.price
        if level is None:
            return None
        hit = b.high >= level if sell else b.low <= level
    return level if hit else None


def _simulate(strategy: DeclarativeStrategy, bars: list[Bar], cash: float) -> ReplayResult:
    """Market orders fill at the bar close; resting stops/limits when a later bar touches them."""
    ledger = LedgerContext(cash=cash)
    strategy.bind(ledger)
    result = ReplayResult()
    resting: list[OrderIntent] = []

    def fill(intent: OrderIntent, price: float, ts: int) -> None:
        trade = Trade(intent.instrument_id, intent.side, intent.quantity, price, ts)
        result.fills.append(trade)
        strategy.handle_fill(trade)

    strategy.on_start()
    for b in bars:
        ledger.set_now(b.ts)
        for intent in list(resting):  # stops are checked first (pessimistic ordering)
            if intent not in resting:
                continue
            price = _triggered(intent, b)
            if price is not None:
                resting.remove(intent)
                for sibling in resting:  # harness-level OCO: the strategy does not cancel
                    ledger.release(sibling)
                resting.clear()
                fill(intent, price, b.ts)
        strategy.on_bar(b)
        for intent in strategy.drain_intents():
            result.intents.append(intent)
            if intent.order_type is OrderType.MARKET:
                fill(intent, b.close, b.ts)
            else:
                resting.append(intent)
    strategy.on_stop()
    return result


def run(
    bars: int = 120,
    fast: int = 5,
    slow: int = 20,
    quantity: float = 10.0,
    stop_pct: float = 0.03,
    target_pct: float = 0.06,
) -> dict[str, Any]:
    """Run the strategy on synthetic bars and return a JSON-serializable report."""
    days = weekdays_func(dt.date(2026, 1, 1), bars)
    closes = _closes(bars)
    series = [
        bar(SYMBOL, d, closes[i - 1] if i else closes[0], close=closes[i])
        for i, d in enumerate(days)
    ]
    strat = LongShortCrossover(fast, slow, quantity, stop_pct, target_pct)
    result = _simulate(strat, series, cash=1_000_000.0)

    return {
        "strategy": {
            "name": strat.name,
            "warmup_bars": strat.warmup_bars,
            "params": {
                "fast": fast,
                "slow": slow,
                "quantity": quantity,
                "stop_pct": stop_pct,
                "target_pct": target_pct,
            },
        },
        "config": {
            "bars": bars,
            "fast": fast,
            "slow": slow,
            "quantity": quantity,
            "stop_pct": stop_pct,
            "target_pct": target_pct,
            "symbol": SYMBOL,
        },
        "entries": jsonable(strat.entries),
        "result": jsonable(result),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--bars", type=int, default=120, help="number of synthetic bars")
    parser.add_argument("--fast", type=int, default=5, help="fast SMA period")
    parser.add_argument("--slow", type=int, default=20, help="slow SMA period")
    parser.add_argument("--quantity", type=float, default=10.0, help="shares per entry")
    parser.add_argument("--stop-pct", type=float, default=0.03, help="stop distance (fraction)")
    parser.add_argument("--target-pct", type=float, default=0.06, help="target distance (fraction)")
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON here")
    args = parser.parse_args(argv)

    report = run(
        bars=args.bars,
        fast=args.fast,
        slow=args.slow,
        quantity=args.quantity,
        stop_pct=args.stop_pct,
        target_pct=args.target_pct,
    )

    text = json.dumps(report, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
