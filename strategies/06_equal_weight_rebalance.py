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

No subclass, no ``on_bar``: the universe is re-read at every rebalance (joiners and leavers
are picked up), names that left are sold, the rest are trimmed or topped up in whole shares.
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

Synthetic bars (see ``tests/synthetic.py``) for all 30 members are replayed by a small
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
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path
from typing import Any

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:  # plain checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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

from honba_examples.jsonable import jsonable
from tests.synthetic import bar
from tests.synthetic import weekdays as weekdays_func

__all__ = ["build_strategy", "build_variant", "main", "run"]

UNIVERSE_KEY = "nifty200_alpha30"
EXCHANGE = "NSE"

NAME = "equal_weight_rebalance"
ALLOCATION = 0.995  # same cash buffer as the catalog strategy's config.toml


def build_strategy(\
    rebalance_days: int = 15, allocation: float = ALLOCATION, name: str = NAME\
) -> PortfolioStrategy:
    """Equal-weight rebalance of the Alpha 30, composed from parts in code."""
    return PortfolioStrategy(
        NamedUniverse(UNIVERSE_KEY, EXCHANGE),
        EqualWeight(),
        EveryNDays(rebalance_days),
        allocation=allocation,
        name=name,
    )


def build_variant(\
    weighting: str = "equal",
    schedule: str = "every:15d",
    allocation: float = ALLOCATION,
) -> PortfolioStrategy:
    """Same universe, other parts, built from TOML-style params (raises ValueError if bad)."""
    params = {
        "universe": UNIVERSE_KEY,
        "exchange": EXCHANGE,
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


def run(\
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
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
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON here")
    args = parser.parse_args(argv)

    report = run(
        bars=args.bars,
        rebalance_days=args.rebalance_days,
        allocation=args.allocation,
        capital=args.capital,
        weighting=args.weighting,
        schedule=args.schedule,
    )

    text = json.dumps(report, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
