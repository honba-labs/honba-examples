"""06_alpha30_equal_weight_demo

Equal-weight Nifty200 Alpha 30, rebalanced every 15 trading days.

Membership is resolved only through the Honba universe API:

    from honba.markets.india.universes import resolve_universe
    ids = resolve_universe("nifty200_alpha_30", exchange="NSE")

Re-fetched on every rebalance so index joiners / leavers are applied.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.markets.india.universes import resolve_universe
from honba.strategies.base import Strategy
from honba.strategies.sizing import whole_shares


class Alpha30EqualWeightRebalance(Strategy):
    name = "alpha30_equal_weight_rebalance"
    UNIVERSE = "nifty200_alpha_30"

    def __init__(
        self,
        capital: float = 1_000_000.0,
        allocation: float = 0.98,
        rebalance_days: int = 15,
        exchange: str = "NSE",
        universe: str = UNIVERSE,
    ) -> None:
        self.capital = capital
        self.allocation = allocation
        self.rebalance_days = rebalance_days
        self.exchange = exchange
        self.universe_name = universe

        self._universe: set[InstrumentId] = set()
        self._last_prices: dict[InstrumentId, float] = {}
        self._last_day: date | None = None
        self._days_since_rebalance = 0
        self._initial_done = False

    def on_start(self) -> None:
        self._universe = set(resolve_universe(self.universe_name, exchange=self.exchange))
        print(f"[start] {self.universe_name} size={len(self._universe)}")

    def on_bar(self, bar: Bar) -> None:
        self._last_prices[bar.instrument_id] = float(bar.close)

        day = _bar_day(bar, self.ctx.now())
        if self._last_day is None:
            self._last_day = day
        if day > self._last_day:
            self._days_since_rebalance += 1
            self._last_day = day

        if not self._initial_done:
            self._rebalance("initial")
            self._initial_done = True
            self._days_since_rebalance = 0
            return

        if self._days_since_rebalance >= self.rebalance_days:
            self._rebalance(f"schedule/{self.rebalance_days}d")
            self._days_since_rebalance = 0

    def _rebalance(self, reason: str) -> None:
        previous = set(self._universe)
        # Engine API — always re-resolve
        self._universe = set(resolve_universe(self.universe_name, exchange=self.exchange))
        if not self._universe:
            print(f"[rebalance:{reason}] empty — skip")
            return

        joined = self._universe - previous
        left = previous - self._universe
        if joined or left:
            print(
                f"[membership] "
                f"+{[i.symbol for i in sorted(joined, key=lambda x: x.symbol)]} "
                f"-{[i.symbol for i in sorted(left, key=lambda x: x.symbol)]}"
            )

        n = len(self._universe)
        target_notional = (self.capital * self.allocation) / n
        held = {iid for iid, qty in self.ctx.positions().items() if qty > 0}

        for iid in held - self._universe:
            qty = self.position(iid)
            if qty > 0 and not self.busy(iid):
                self.sell(iid, qty)
                print(f"  EXIT {iid.symbol} qty={qty}")

        for iid in self._universe:
            if self.busy(iid):
                continue
            px = self._last_prices.get(iid)
            if px is None or px <= 0:
                continue
            target_qty = whole_shares(target_notional, 1.0, px)
            diff = target_qty - self.position(iid)
            if abs(diff) < 1:
                continue
            if diff > 0:
                self.buy(iid, diff)
                print(f"  BUY  {iid.symbol} +{diff}")
            else:
                self.sell(iid, -diff)
                print(f"  SELL {iid.symbol} {-diff}")

        print(f"[rebalance:{reason}] members={n} target≈{target_notional:,.0f}")


def _bar_day(bar: Bar, now_ns: int) -> date:
    ts: Any = getattr(bar, "ts", None) or getattr(bar, "ts_event", None)
    if ts is not None and hasattr(ts, "date"):
        return ts.date()
    if isinstance(ts, (int, float)):
        v = float(ts)
        if v > 1e14:
            v /= 1e9
        elif v > 1e11:
            v /= 1e3
        return datetime.fromtimestamp(v, tz=timezone.utc).date()
    if now_ns > 0:
        return datetime.fromtimestamp(now_ns / 1e9, tz=timezone.utc).date()
    return datetime.now(tz=timezone.utc).date()


if __name__ == "__main__":
    # Smoke: resolve via engine API (fails until Alpha 30 is registered with members)
    try:
        members = resolve_universe("nifty200_alpha_30")
        print(f"nifty200_alpha_30 → {len(members)} names")
    except ValueError as e:
        print(f"universe not ready: {e}")
    print("Strategy uses resolve_universe() only — no hardcoded constituents.")