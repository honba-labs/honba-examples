"""stocksharp/05_risk_parity: Risk-parity portfolio rebalancer (port of StockSharp risk sample).

Strategy: dynamic allocation across 10 NIFTY scrips, weighted inversely to their
60-day historical volatility (risk parity: equal risk contribution). Built with
``honba.strategies.PortfolioStrategy`` from swappable parts —
``StaticUniverse`` + ``InverseVolatility`` + ``EveryNDays`` — exactly the
composition pattern of ``strategies/06_equal_weight_rebalance.py``, then replayed
on synthetic bars with a ``LedgerContext`` (the same manual replay harness that
example 06 uses: each order fills at its instrument's last close).

Each symbol gets a distinct volatility regime (low to high), so inverse-vol
weights concentrate in the quiet scrips.  Rebalance cadence is every 20 sessions
with a 65-bar history buffer (60-day vol lookback + warm-up).

Runs offline and deterministic; no adapter, no Parquet store.  Sizes are whole
shares via the framework; lot size and freeze limits are not applied here
(see ``02_twap_vwap.py`` for the freeze-limit slicer).

Run::

    python examples/stocksharp/05_risk_parity.py --symbols 10 --bars 260 --rebalance-days 20 --seed 42
    python examples/stocksharp/05_risk_parity.py --symbols 10 --bars 260 --rebalance-days 10 --out risk_parity.json

Pitfalls::

    - Inverse-vol weights use the history available at each rebalance; the first
      rebalance happens only after the 65-bar history buffer fills, so early
      sessions carry equal weight until the vol lookback is populated.
    - Volatility is backward-looking (60-day sample stdev of simple returns) and
      regime changes catch the weights stale until the next rebalance.
    - Weights are long-only, sum to the allocation fraction (0.98); the residual
      stays in cash.  Shorts, leverage and concentration caps are not applied.
    - Fills are at each instrument's last close at rebalance time — a one-bar
      lag vs live execution, and a name not yet fed in that session fills stale.
    - Synthetic geometric random walks have stationary vol by construction; real
      scrip vols cluster and jump, so risk parity on real data needs vol
      forecasting, not just trailing stdev.
"""

from __future__ import annotations

import random
import sys
from datetime import date
from typing import Any

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.trade import Trade
from honba.strategies import PortfolioStrategy
from honba.strategies.context import LedgerContext
from honba.strategies.portfolio import EveryNDays, InverseVolatility, StaticUniverse

from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.synthetic import session_ts, weekdays

__all__ = ["main", "run"]

DEFAULT_SYMBOLS = 10
DEFAULT_BARS = 260
DEFAULT_REBALANCE_DAYS = 20
DEFAULT_CAPITAL = 1_000_000.0
VOL_LOOKBACK = 60
HISTORY_LEN = VOL_LOOKBACK + 5


def _symbol_bars(
    symbol: str,
    sessions: list[date],
    vol: float,
    seed: int,
    base: float = 1000.0,
    drift: float = 0.02,
) -> list[Bar]:
    """One synthetic scrip: geometric random walk with its own vol and seed."""
    rng = random.Random(seed)
    bars: list[Bar] = []
    price = base
    for day in sessions:
        price = max(price * (1.0 + drift + rng.gauss(0.0, vol)), 1.0)
        bars.append(
            Bar(
                InstrumentId(symbol, "NSE"),
                session_ts(day),
                price,
                price,
                price,
                price,
                1e6,
            )
        )
    return bars


def _build_bars(
    symbols: int = DEFAULT_SYMBOLS,
    bars: int = DEFAULT_BARS,
    seed: int = 42,
) -> list[Bar]:
    """Deterministic multi-scrip bars: one session index shared by all symbols."""
    sessions = weekdays(date(2026, 1, 5), bars)
    out: list[Bar] = []
    for i in range(symbols):
        symbol = f"SCRIP{i + 1:02d}"
        vol = 0.005 + i * 0.004  # 0.5% to 4.1% daily vol across the universe
        out.extend(_symbol_bars(symbol, sessions, vol, seed + 100 + i))
    # Interleaved by session (like a real tape) so every symbol's history grows
    # in step; deterministic given the inputs.
    out.sort(key=lambda b: (b.ts, b.instrument_id.symbol))
    return out


def run(
    symbols: int = DEFAULT_SYMBOLS,
    bars: int = DEFAULT_BARS,
    rebalance_days: int = DEFAULT_REBALANCE_DAYS,
    seed: int = 42,
    capital: float = DEFAULT_CAPITAL,
    allocation: float = 0.98,
) -> dict[str, Any]:
    """Risk parity over ``symbols`` scrips via PortfolioStrategy + manual replay."""
    bar_list = _build_bars(symbols=symbols, bars=bars, seed=seed)
    instruments = [InstrumentId(f"SCRIP{i + 1:02d}", "NSE") for i in range(symbols)]

    strat = PortfolioStrategy(
        universe=StaticUniverse(instruments),
        weighting=InverseVolatility(lookback=VOL_LOOKBACK),
        schedule=EveryNDays(rebalance_days),
        allocation=allocation,
        history_len=HISTORY_LEN,
        name="risk_parity",
    )
    strat.bind(LedgerContext(cash=capital))

    last_close: dict[InstrumentId, float] = {}
    rebalance_sessions: set[int] = set()
    strat.on_start()
    for b in bar_list:
        last_close[b.instrument_id] = b.close
        strat.on_bar(b)
        intents = list(strat.drain_intents())
        if intents:
            rebalance_sessions.add(b.ts)
            for intent in intents:
                fill_price = last_close[intent.instrument_id]
                strat.handle_fill(
                    Trade(
                        intent.instrument_id,
                        intent.side,
                        intent.quantity,
                        fill_price,
                        b.ts,
                    )
                )
    strat.on_stop()

    positions = strat.ctx.positions()
    closes = {b.instrument_id: b.close for b in bar_list}  # final session wins
    equity = strat.ctx.cash().to_major() + sum(q * closes[i] for i, q in positions.items())
    target_weight = allocation / symbols
    weights = []
    for i in instruments:
        w = positions.get(i, 0.0) * closes.get(i, 0.0) / equity if equity > 0 else 0.0
        weights.append({"symbol": i.symbol, "quantity": positions.get(i, 0.0), "weight": w})

    return {
        "strategy": {
            "name": strat.name,
            "warmup_bars": strat.warmup_bars,
            "params": {
                "symbols": symbols,
                "bars": bars,
                "rebalance_days": rebalance_days,
                "vol_lookback": VOL_LOOKBACK,
                "allocation": allocation,
            },
        },
        "config": {
            "symbols": symbols,
            "bars": bars,
            "rebalance_days": rebalance_days,
            "capital": capital,
            "allocation": allocation,
            "seed": seed,
        },
        "summary": {
            "universe_size": symbols,
            "rebalances": len(rebalance_sessions),
            "final_equity": equity,
            "final_cash": strat.ctx.cash().to_major(),
            "target_weight": target_weight,
            "max_weight_deviation": (
                max(abs(h["weight"] - target_weight) for h in weights) if weights else 0.0
            ),
            "positions_handled": sum(1 for h in weights if h["quantity"] != 0),
        },
        "holdings": sorted(weights, key=lambda h: h["symbol"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--symbols", type=int, default=DEFAULT_SYMBOLS)
    parser.add_argument("--bars", type=int, default=DEFAULT_BARS)
    parser.add_argument("--rebalance-days", type=int, default=DEFAULT_REBALANCE_DAYS)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument("--allocation", type=float, default=0.98)
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(
        symbols=args.symbols,
        bars=args.bars,
        rebalance_days=args.rebalance_days,
        seed=args.seed,
        capital=args.capital,
        allocation=args.allocation,
    )

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
