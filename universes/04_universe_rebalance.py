"""04_universe_rebalance

Generic equal-weight rebalancer that works with *any* named universe.

Demonstrates the three actions required on every rebalance day:

1. Sell stocks that have left the index.
2. Buy stocks that have been added.
3. Trim overweight (relative winners) and top-up underweight
   (relative laggards) so every surviving name is again equal-weight.

Default universe = Nifty200 Alpha 30, rebalance every 15 trading days.
Reuse the same class for Nifty 50, Bank Nifty, custom lists, etc. by
passing a different ``universe`` name.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.markets.india.universes import resolve_universe
from honba.strategies.base import Strategy
from honba.strategies.sizing import whole_shares

# Re-use the helper from 03 so the seed is registered if needed
from universes import load_alpha30  # relative import when run as package
# fallback for standalone execution:
try:
    from .03_alpha30_constituents import load_alpha30, UNIVERSE_NAME
except ImportError:
    from importlib.util import spec_from_file_location, module_from_spec
    import pathlib
    _p = pathlib.Path(__file__).with_name("03_alpha30_constituents.py")
    _spec = spec_from_file_location("alpha30", _p)
    _mod = module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    load_alpha30 = _mod.load_alpha30
    UNIVERSE_NAME = _mod.UNIVERSE_NAME


class UniverseEqualWeightRebalance(Strategy):
    """Equal-weight portfolio of a named universe, rebalanced every N days."""

    name = "universe_equal_weight_rebalance"

    def __init__(
        self,
        capital: float = 1_000_000.0,
        allocation: float = 0.98,
        rebalance_days: int = 15,
        venue: str = "NSE",
        universe: str = UNIVERSE_NAME,
    ) -> None:
        self.capital = capital
        self.allocation = allocation
        self.rebalance_days = rebalance_days
        self.venue = venue
        self.universe_name = universe

        self._universe: set[InstrumentId] = set()
        self._last_prices: dict[InstrumentId, float] = {}
        self._last_day: date | None = None
        self._days_since_rebalance = 0
        self._initial_done = False

    # ------------------------------------------------------------------
    # Membership
    # ------------------------------------------------------------------
    def _resolve(self) -> set[InstrumentId]:
        """Always go through the engine API (or the 03 fallback)."""
        try:
            return set(resolve_universe(self.universe_name, venue=self.venue))
        except ValueError:
            # Engine does not know the name yet → use the Alpha-30 helper
            if self.universe_name in ("nifty200_alpha_30", "alpha30"):
                return set(load_alpha30(self.venue))
            raise

    # ------------------------------------------------------------------
    # Strategy hooks
    # ------------------------------------------------------------------
    def on_start(self) -> None:
        self._universe = self._resolve()
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

    # ------------------------------------------------------------------
    # Core rebalance logic (the three required actions)
    # ------------------------------------------------------------------
    def _rebalance(self, reason: str) -> None:
        previous = set(self._universe)
        self._universe = self._resolve()

        if not self._universe:
            print(f"[rebalance:{reason}] empty universe — skip")
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

        # 1. Fully exit names that left the list
        held = {iid for iid, qty in self.ctx.positions().items() if qty > 0}
        for iid in held - self._universe:
            qty = self.position(iid)
            if qty > 0 and not self.busy(iid):
                self.sell(iid, qty)
                print(f"  EXIT {iid.symbol} qty={qty}")

        # 2 + 3. Enter new names and re-equalise survivors
        #    (over-weight → sell, under-weight → buy)
        for iid in self._universe:
            if self.busy(iid):
                continue
            px = self._last_prices.get(iid)
            if px is None or px <= 0:
                continue

            target_qty = whole_shares(target_notional, 1.0, px)
            diff = target_qty - self.position(iid)
            if abs(diff) < 1:          # ignore sub-share noise
                continue
            if diff > 0:
                self.buy(iid, diff)
                print(f"  BUY  {iid.symbol} +{diff}")
            else:
                self.sell(iid, -diff)
                print(f"  SELL {iid.symbol} {-diff}")

        print(f"[rebalance:{reason}] members={n} target≈{target_notional:,.0f}")


def _bar_day(bar: Bar, now_ns: int) -> date:
    """Extract a calendar date from a bar or the context clock."""
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
    return date.today()


if __name__ == "__main__":
    # Quick smoke test of membership resolution
    members = load_alpha30()
    print(f"nifty200_alpha_30 → {len(members)} names (via 03 helper)")
    print("04_universe_rebalance ready — drop into a StrategyRunner.")