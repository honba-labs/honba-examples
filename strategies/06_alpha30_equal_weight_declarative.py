"""strategies/06_alpha30_equal_weight_declarative: Alpha 30 equal-weight, declared not coded.

The same portfolio as the catalog strategy, written with
``honba.strategies.TargetWeightStrategy``: say *what* the portfolio is, let the framework
do the order mechanics.

Side by side
------------
Imperative reference (``../honba-strategies/universe/alpha/equal_weight/strategy.py``,
~190 lines, you write all of it)::

    - read params from StrategyConfig (capital, allocation, rebalance_days, exchange)
    - resolve the universe in on_start AND again at every rebalance, with a seed fallback
      that mutates the global UNIVERSES registry
    - track last prices, the previous bar day and a days-since-rebalance counter
    - decide "first rebalance" (all members priced, or the day rolled over)
    - mark the portfolio to market (cash + positions at last close)
    - diff old vs new membership and log EVENT_MEMBERSHIP_ADD / _DEL
    - sell leavers, size each name with whole_shares, buy or trim the difference
    - skip busy / unpriced names, parse bar timestamps by hand (_bar_day)

Declared here (the whole strategy)::

    class Alpha30EqualWeightDeclarative(TargetWeightStrategy):
        name = "alpha30_equal_weight_declarative"
        def universe(self):
            return resolve_universe("nifty200_alpha30", exchange="NSE")

``target_weights()`` is not overridden: the default is equal weight over ``universe()``.
``rebalance_days`` (default 15) and ``allocation`` (default 0.98) are class attributes;
override them to change the cadence or keep a cash buffer. The framework values the
portfolio, exits names that left the universe, trims, tops up, logs membership changes and
never uses the wall clock. Because ``universe()`` is called at every rebalance, index
reconstitutions are picked up with no extra code.

Fallback: the engine knows ``nifty200_alpha30``. If an older engine does not, ``universe()``
uses a local seed list built into ``InstrumentId`` values; the global registry is not touched.

Synthetic bars (see ``tests/synthetic.py``) for all 30 members are replayed by a small
harness (``honba.strategies.testing.replay`` fills every order at the price of the bar that
emitted it, which is wrong for a portfolio, so each order here fills at the last close of its
own instrument): no adapter, no Parquet store, no network. The report
holds the number of rebalances (distinct sessions that submitted orders), the final
quantity and weight of every name, and the largest deviation from the target weight
(``allocation / n``) at the last bar, which includes price drift since the last rebalance.

Run::

    python strategies/06_alpha30_equal_weight_declarative.py
    python strategies/06_alpha30_equal_weight_declarative.py --bars 120 --rebalance-days 10
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:  # plain checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.trade import Trade
from honba.markets.india.universes import resolve_universe
from honba.strategies import TargetWeightStrategy
from honba.strategies.context import LedgerContext
from honba.strategies.testing import ReplayResult

from honba_examples.jsonable import jsonable
from tests.synthetic import bar
from tests.synthetic import weekdays as weekdays_func

__all__ = ["Alpha30EqualWeightDeclarative", "main", "run"]

UNIVERSE_KEY = "nifty200_alpha30"
EXCHANGE = "NSE"

# Used ONLY if the engine does not know UNIVERSE_KEY (learning / CI environments with an
# older engine). It is turned into InstrumentIds locally; no global registry is mutated.
_SEED: tuple[str, ...] = (
    "ADANIPOWER", "SHRIRAMFIN", "HINDALCO", "ADANIGREEN", "EICHERMOT",
    "ADANIENSOL", "IDEA", "BHEL", "CUMMINSIND", "POWERINDIA",
    "POLYCAB", "MUTHOOTFIN", "PAYTM", "INDIANB", "LAURUSLABS",
    "VEDL", "BHARATFORG", "NYKAA", "ASHOKLEY", "MCX",
    "FEDERALBNK", "AUBANK", "LTF", "SAIL", "GLENMARK",
    "FORTIS", "NATIONALUM", "BSE", "ABCAPITAL", "DIXON",
)  # fmt: skip


class Alpha30EqualWeightDeclarative(TargetWeightStrategy):
    """Equal weight across the Nifty200 Alpha 30; everything else is the framework."""

    name = "alpha30_equal_weight_declarative"

    def universe(self) -> Iterable[InstrumentId]:
        try:
            return resolve_universe(UNIVERSE_KEY, exchange=EXCHANGE)
        except ValueError:  # engine does not know the name yet: use the seed, registry untouched
            return [InstrumentId(symbol, EXCHANGE) for symbol in _SEED]


def _synthetic_bars(symbols: list[str], sessions: int) -> list:
    """Deterministic daily bars: each name has its own base price, drift and wobble."""
    days = weekdays_func(dt.date(2026, 5, 4), sessions)
    bars = []
    for k, symbol in enumerate(symbols):
        base = 100.0 + 37.0 * k
        slope = 0.002 * (1 if k % 3 else -1)
        for i, day in enumerate(days):
            px = base * (1 + slope * i + 0.01 * math.sin(i + k))
            bars.append(bar(symbol, day, round(px, 2), close=round(px * 1.003, 2)))
    bars.sort(key=lambda b: (b.ts, b.instrument_id.symbol))
    return bars


def _replay_at_last_close(strategy: TargetWeightStrategy, bars: list[Bar]) -> ReplayResult:
    """Feed bars in order; fill each emitted market order at its instrument's last close."""
    result = ReplayResult()
    last_close: dict[InstrumentId, float] = {}
    strategy.on_start()
    for b in bars:
        last_close[b.instrument_id] = b.close
        strategy.ctx.set_now(b.ts)
        strategy.on_bar(b)
        for intent in strategy.drain_intents():
            result.intents.append(intent)
            fill = Trade(
                intent.instrument_id,
                intent.side,
                intent.quantity,
                last_close[intent.instrument_id],
                b.ts,
            )
            result.fills.append(fill)
            strategy.handle_fill(fill)
    strategy.on_stop()
    return result


def run(
    bars: int = 60,
    rebalance_days: int = 15,
    allocation: float = 0.98,
    capital: float = 1_000_000.0,
) -> dict[str, Any]:
    """Replay the strategy on synthetic bars and return a JSON-serializable report."""
    strat = Alpha30EqualWeightDeclarative()
    strat.rebalance_days = rebalance_days
    strat.allocation = allocation
    strat.bind(LedgerContext(cash=capital))

    members = sorted(strat.universe(), key=lambda i: (i.symbol, i.exchange))
    symbols = [i.symbol for i in members]
    series = _synthetic_bars(symbols, bars)
    result = _replay_at_last_close(strat, series)

    closes = {b.instrument_id: b.close for b in series}  # last write wins: final session
    positions = strat.ctx.positions()
    equity = strat.ctx.cash().to_major() + sum(q * closes[i] for i, q in positions.items())
    target = allocation / len(members)
    holdings = [
        {
            "symbol": i.symbol,
            "quantity": positions.get(i, 0.0),
            "close": closes[i],
            "weight": positions.get(i, 0.0) * closes[i] / equity,
        }
        for i in members
    ]
    rebalance_sessions = {f.ts for f in result.fills}

    return {
        "strategy": {
            "name": strat.name,
            "warmup_bars": strat.warmup_bars,
            "params": {"rebalance_days": rebalance_days, "allocation": allocation},
        },
        "config": {
            "bars": bars,
            "capital": capital,
            "allocation": allocation,
            "rebalance_days": rebalance_days,
            "universe": UNIVERSE_KEY,
        },
        "summary": {
            "universe_size": len(members),
            "rebalances": len(rebalance_sessions),
            "n_fills": len(result.fills),
            "final_equity": equity,
            "final_cash": strat.ctx.cash().to_major(),
            "target_weight": target,
            "max_weight_deviation": max(abs(h["weight"] - target) for h in holdings),
        },
        "holdings": holdings,
        "result": jsonable(result),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bars", type=int, default=60, help="number of synthetic sessions")
    parser.add_argument("--rebalance-days", type=int, default=15, help="trading days between")
    parser.add_argument("--allocation", type=float, default=0.98, help="fraction of equity")
    parser.add_argument("--capital", type=float, default=1_000_000.0, help="starting cash")
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON here")
    args = parser.parse_args(argv)

    report = run(
        bars=args.bars,
        rebalance_days=args.rebalance_days,
        allocation=args.allocation,
        capital=args.capital,
    )

    text = json.dumps(report, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
