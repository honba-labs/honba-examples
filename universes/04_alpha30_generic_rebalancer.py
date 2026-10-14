"""04_alpha30_generic_rebalancer

Generic equal-weight rebalancer that works with *any* named universe.

Demonstrates the three actions required on every rebalance day:

1. Sell stocks that have left the index.
2. Buy stocks that have been added.
3. Trim overweight (relative winners) and top-up underweight
   (relative laggards) so every surviving name is again equal-weight.

Default universe = Nifty200 Alpha 30, rebalance every 15 trading days.
Reuse the same class for Nifty 50, Bank Nifty, custom lists, etc. by
passing a different ``universe`` name (see 05_alpha30_custom_universe.py).

The strategy class is ``UniverseEqualWeightRebalance``; the ``__main__`` block
only resolves membership as a smoke test, so it runs offline and deterministically
against the bundled universe map.

Run::

    python universes/04_alpha30_generic_rebalancer.py

Pitfalls::

    - Joiners and leavers need history: _rebalance resolves membership with as_of=<bar day>, so
      they only occur for universes with a registered point-in-time history. Without one it warns
      and uses today's list. The shipped Alpha 30 history is today's 30 names stamped as members
      from 2005-04-01 (core), so as_of= changes nothing there until a real history replaces it.
    - Prices: sizing uses the last close per instrument and skips any name whose close is more
      than max_price_age_days (4) old on the rebalance day. The initial rebalance still fires on
      the first bar of a new day or once every name has printed, so it can size off partial data.
    - Capital: target_notional is (cash + marked holdings) x allocation / n, falling back to the
      constructor capital only when that equity is not positive. The strategy never checks cash
      itself; fills, costs and settlement belong to the runner you plug this into.
    - Bar dates come from the bar or the context clock; with neither, _bar_day raises rather than
      reading the wall clock. The "every 15 days" counter counts days on which any instrument
      printed, not exchange sessions.
    - Nothing here is backtested: __main__ only resolves membership, so no performance follows.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.markets.india.universes import resolve_universe
from honba.strategies.base import Strategy
from honba.strategies.sizing import whole_shares

try:
    import alpha30_constituents
except ImportError:
    from universes import alpha30_constituents  # type: ignore[import-not-found]


load_alpha30 = alpha30_constituents.load_alpha30
UNIVERSE_NAME: str = alpha30_constituents.UNIVERSE_NAME


class UniverseEqualWeightRebalance(Strategy):
    """Equal-weight portfolio of a named universe, rebalanced every N days."""

    name = "universe_equal_weight_rebalance"

    def __init__(
        self,
        capital: float = 1_000_000.0,
        allocation: float = 0.98,
        rebalance_days: int = 15,
        exchange: str = "NSE",
        universe: str = UNIVERSE_NAME,
        max_price_age_days: int = 4,
    ) -> None:
        self.capital = capital
        self.max_price_age_days = max_price_age_days
        self.allocation = allocation
        self.rebalance_days = rebalance_days
        self.exchange = exchange
        self.universe_name = universe

        self._universe: set[InstrumentId] = set()
        self._last_prices: dict[InstrumentId, float] = {}
        self._price_day: dict[InstrumentId, date] = {}
        self._warned_no_history = False
        self._last_day: date | None = None
        self._days_since_rebalance = 0
        self._initial_done = False

    # ------------------------------------------------------------------
    # Membership
    # ------------------------------------------------------------------
    def _resolve(self, as_of: date | None = None) -> set[InstrumentId]:
        """Resolve through the engine API, point-in-time when ``as_of`` is given.

        With ``as_of`` the registered history is used; a universe with no history raises in the
        core, so we warn once and fall back to today's list (survivorship-biased) rather than die.
        """
        if as_of is not None:
            try:
                return set(
                    resolve_universe(self.universe_name, exchange=self.exchange, as_of=as_of)
                )
            except ValueError:
                if not self._warned_no_history:
                    print(
                        f"[membership] no point-in-time history for {self.universe_name!r}; "
                        "using today's list (joiners/leavers cannot occur)"
                    )
                    self._warned_no_history = True
        try:
            return set(resolve_universe(self.universe_name, exchange=self.exchange))
        except ValueError:
            # Engine does not know the name yet → use the Alpha-30 helper
            if self.universe_name in ("nifty200_alpha_30", "alpha30"):
                return set(load_alpha30(self.exchange))
            raise

    # ------------------------------------------------------------------
    # Strategy hooks
    # ------------------------------------------------------------------
    def on_start(self) -> None:
        self._universe = self._resolve()
        print(f"[start] {self.universe_name} size={len(self._universe)}")

    def on_bar(self, bar: Bar) -> None:
        day = _bar_day(bar, self.ctx.now())
        self._last_prices[bar.instrument_id] = float(bar.close)
        self._price_day[bar.instrument_id] = day
        day_changed = False
        if self._last_day is None:
            self._last_day = day
        if day > self._last_day:
            self._days_since_rebalance += 1
            self._last_day = day
            day_changed = True

        if not self._initial_done:
            if len(self._last_prices) >= len(self._universe) or day_changed:
                self._rebalance("initial", day)
                self._initial_done = True
                self._days_since_rebalance = 0
            return

        if self._days_since_rebalance >= self.rebalance_days:
            self._rebalance(f"schedule/{self.rebalance_days}d", day)
            self._days_since_rebalance = 0

    # ------------------------------------------------------------------
    # Core rebalance logic (the three required actions)
    # ------------------------------------------------------------------
    def _fresh_price(self, iid: InstrumentId, day: date) -> float | None:
        """Last close if it is at most ``max_price_age_days`` old on ``day``, else None."""
        px = self._last_prices.get(iid)
        seen = self._price_day.get(iid)
        if px is None or seen is None or px <= 0:
            return None
        if (day - seen).days > self.max_price_age_days:
            return None
        return px

    def _equity(self) -> float:
        """Cash plus marked holdings from the context; the constructor capital if that is 0."""
        equity = self.ctx.cash().to_major()
        for iid, qty in self.ctx.positions().items():
            equity += qty * self._last_prices.get(iid, 0.0)
        return equity if equity > 0 else self.capital

    def _rebalance(self, reason: str, day: date) -> None:
        previous = set(self._universe)
        self._universe = self._resolve(day)

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
        target_notional = (self._equity() * self.allocation) / n

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
            px = self._fresh_price(iid, day)
            if px is None:
                print(f"  SKIP {iid.symbol} (no price within {self.max_price_age_days}d)")
                continue

            target_qty = whole_shares(target_notional, 1.0, px)
            diff = target_qty - self.position(iid)
            if abs(diff) < 1:  # ignore sub-share noise
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
    raise ValueError(
        "bar has no usable timestamp and the context clock is 0; refusing to fall back to the "
        "wall clock (replays must be reproducible)"
    )


if __name__ == "__main__":
    # Quick smoke test of membership resolution
    members = load_alpha30()
    print(f"nifty200_alpha_30 → {len(members)} names (via 03 helper)")
    print("04_universe_rebalance ready — drop into a StrategyRunner.")
