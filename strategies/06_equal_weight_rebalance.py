"""strategies/06_equal_weight_rebalance: an equal-weight rebalance, composed from parts.

Alpha 30 is not a strategy, it is a *universe* (a parameter). The strategy is "hold every
member of a universe at an equal weight and rebalance periodically". The portfolio layer
(``honba.strategies.portfolio``) splits that into swappable parts, and ``PortfolioStrategy``
wires them together::

    Universe -> Selector -> WeightingScheme -> RebalanceSchedule -> PortfolioStrategy
    (who)       (which)     (how much)         (when)               (orders)

Composed in code (``build_strategy`` below, the whole strategy)::

    PortfolioStrategy(\
        NamedUniverse(\"nifty200_alpha30\"),   # the only Alpha-30-specific thing: a name
        EqualWeight(),                       # swap: InverseVolatility()
        EveryNDays(15),                      # swap: MonthlyFirstSession()
        allocation=0.995,\
    )

No subclass, no ``on_bar``: the universe is re-read at every rebalance as of the bar date
(``point_in_time=True``), names that left are sold, the rest are trimmed or topped up in whole
shares. Caveat: the engine's built-in Alpha 30 history is the current list with every name
included since 2005-04-01, not real index history, so in this repo nobody joins or leaves; real
joiners and leavers need ``register_universe_history`` or a ``constituents/`` catalog file.
Everything is deterministic and never reads the wall clock.

The same strategy as plain params (what a ``config.toml`` ``[params]`` table holds)::

    [params]
    universe   = \"nifty200_alpha30\"
    weighting  = \"equal\"            # or \"inverse_vol\" / \"inverse_vol:30\"
    schedule   = \"every:15d\"        # or \"monthly:first_session\" / \"drift:0.05\"
    allocation = 0.995

    PortfolioStrategy.from_params(params)   # == build_portfolio_strategy(params)

The catalog strategy ``../honba-strategies/portfolio/rebalancing/equal_weight/`` is exactly
that config bound to a thin ``PortfolioStrategy`` subclass; an integration test checks that
this code-composed version and the catalog one produce identical fills, positions and cash.

Three base classes, from most to least opinionated (``honba.strategies``):
``DeclarativeStrategy`` (per-instrument entries and exits), ``TargetWeightStrategy`` (declare
``universe()`` / ``target_weights()``) and ``PortfolioStrategy`` (compose the parts above).

Other strategies from the same parts: ``--weighting inverse_vol`` (lower-volatility names get
more weight) and ``--schedule monthly:first_session`` (rebalance on the first session of each
month). Those flags go through ``build_portfolio_strategy``, i.e. the params form above.

Synthetic bars (see ``honba_examples/synthetic.py``) for all 30 members are replayed by a small
harness (``honba.strategies.testing.replay`` fills every order at the price of the bar that
emitted it, which is wrong for a portfolio, so each order here fills at the last close of its
own instrument): no adapter, no Parquet store, no network. The report holds the number of
rebalances (distinct sessions that submitted orders), the final quantity and weight of every
name and, for equal weight, the largest deviation from the target weight (``allocation / n``)
at the last bar, which includes price drift since the last rebalance.

Run::

    python strategies/06_equal_weight_rebalance.py
    python strategies/06_equal_weight_rebalance.py --bars 120 --rebalance-days 10
    python strategies/06_equal_weight_rebalance.py --weighting inverse_vol
    python strategies/06_equal_weight_rebalance.py --schedule monthly:first_session

Pitfalls::

    - Today's membership: the universe is point-in-time (as_of is honoured), but the engine's
      registered nifty200_alpha30 history is today's 30 names flagged as members since 2005, not
      real history. Over any real past period that is survivorship bias; register true
      constituent intervals before trusting a long backtest.
    - Invented market: the 30 series are deterministic (base 100 + 37k, slope +/-0.002 per bar, a
      sine wobble, close = open x 1.003). Rebalance counts and weight deviation describe mechanics,
      not any market.
    - Own fill model: _replay_at_last_close fills each order at its instrument's last close at the
      moment it is emitted. That is trading the close, and a name whose bar for that session has not
      been fed yet fills at its previous close (stale). The engine's next-open model would be later.
    - Turnover and costs: the default run makes 105 fills over 4 rebalances with no brokerage, STT
      or slippage. Small trims every few days (or drift bands) are cheap here and are not after
      costs; compare schedules on net numbers.
    - Defence shown: whole-share sizing against allocation=0.995 leaves a cash buffer,
      max_weight_deviation reports drift at the last bar, and nothing reads the wall clock.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.trade import Trade
from honba.strategies.context import LedgerContext
from honba.strategies.portfolio import (
    EqualWeight,
    EveryNDays,
    NamedUniverse,
    PortfolioStrategy,
    build_portfolio_strategy,
)
from honba.strategies.testing import ReplayResult

from honba_examples.cli import run_example
from honba_examples.jsonable import jsonable
from honba_examples.synthetic import bar
from honba_examples.synthetic import weekdays as weekdays_func

__all__ = ["build_strategy", "build_variant", "main", "run"]

UNIVERSE_KEY = "nifty200_alpha30"
EXCHANGE = "NSE"

NAME = "equal_weight_rebalance"
ALLOCATION = 0.995  # same cash buffer as the catalog strategy's config.toml


def build_strategy(
    rebalance_days: int = 15, allocation: float = ALLOCATION, name: str = NAME
) -> PortfolioStrategy:
    """Equal-weight rebalance of the Alpha 30, composed from parts in code."""
    return PortfolioStrategy(
        NamedUniverse(UNIVERSE_KEY, EXCHANGE, point_in_time=True),
        EqualWeight(),
        EveryNDays(rebalance_days),
        allocation=allocation,
        name=name,
    )


def build_variant(
    weighting: str = "equal",
    schedule: str = "every:15d",
    allocation: float = ALLOCATION,
) -> PortfolioStrategy:
    """Same universe, other parts, built from TOML-style params (raises ValueError if bad)."""
    params = {
        "universe": UNIVERSE_KEY,
        "exchange": EXCHANGE,
        "point_in_time": True,
        "weighting": weighting,
        "schedule": schedule,
        "allocation": allocation,
    }
    return build_portfolio_strategy(params, name="portfolio_rebalance")


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


def _replay_at_last_close(strategy: PortfolioStrategy, bars: list[Bar]) -> ReplayResult:
    """Feed bars in order; fill each emitted market order at its instrument's last close."""
    result = ReplayResult()
    last_close: dict[InstrumentId, float] = {}
    strategy.on_start()
    for b in bars:
        last_close[b.instrument_id] = b.close
        if isinstance(strategy.ctx, LedgerContext):
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
    allocation: float = ALLOCATION,
    capital: float = 1_000_000.0,
    weighting: str | None = None,
    schedule: str | None = None,
) -> dict[str, Any]:
    """Replay the strategy on synthetic bars and return a JSON-serializable report.

    With neither ``weighting`` nor ``schedule`` the strategy is composed in code; otherwise it
    is built from params via ``build_portfolio_strategy`` (``schedule`` defaults to
    ``every:<rebalance_days>d``).
    """
    equal = weighting in (None, "equal")
    if weighting is None and schedule is None:
        strat = build_strategy(rebalance_days, allocation)
        schedule_text = f"every:{rebalance_days}d"
    else:
        schedule_text = schedule or f"every:{rebalance_days}d"
        strat = build_variant(weighting or "equal", schedule_text, allocation)
    strat.bind(LedgerContext(cash=capital))

    members = sorted(strat.universe(), key=lambda i: (i.symbol, i.exchange))
    symbols = [i.symbol for i in members]
    series = _synthetic_bars(symbols, bars)
    result = _replay_at_last_close(strat, series)

    closes = {b.instrument_id: b.close for b in series}  # last write wins: final session
    positions = strat.ctx.positions()
    equity = strat.ctx.cash().to_major() + sum(q * closes[i] for i, q in positions.items())
    target = allocation / len(members) if equal else None
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
            "weighting": weighting or "equal",
            "schedule": schedule_text,
        },
        "summary": {
            "universe_size": len(members),
            "rebalances": len(rebalance_sessions),
            "n_fills": len(result.fills),
            "final_equity": equity,
            "final_cash": strat.ctx.cash().to_major(),
            "target_weight": target,
            "max_weight_deviation": (
                max(abs(h["weight"] - target) for h in holdings) if target is not None else None
            ),
        },
        "holdings": holdings,
        "result": jsonable(result),
    }


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bars", type=int, default=60, help="number of synthetic sessions")
    parser.add_argument("--rebalance-days", type=int, default=15, help="trading days between")
    parser.add_argument("--allocation", type=float, default=ALLOCATION, help="fraction of equity")
    parser.add_argument(
        "--weighting", default=None, help="equal | inverse_vol | inverse_vol:<lookback>"
    )
    parser.add_argument(
        "--schedule", default=None, help="every:<N>d | monthly:first_session | drift:<x>"
    )
    parser.add_argument(
        "--initial-capital",
        "--capital",
        dest="capital",
        type=float,
        default=1_000_000.0,
        help="Initial capital in INR / starting cash (default: 1000000.0)",
    )


def main(argv: list[str] | None = None) -> int:
    return run_example(run, __doc__, _add_args, argv=argv)


if __name__ == "__main__":
    sys.exit(main())
