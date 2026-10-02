"""Example: Run backtest and training for Alpha 30 Factor Strategy.

Configures data-fetcher / Parquet store, gets data for 30 equities from NSE NIFTY200 ALPHA 30,
runs in-sample training (2022-01-01 to 2025-12-30) and out-of-sample backtesting (2026-01-01 to today).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent
from honba.entities.trade import Trade
from honba.markets.india.universes import resolve_universe
from honba.screener.coverage import DateInterval
from honba.screener.store import ParquetBarStore
from honba.strategies.config import StrategyConfig
from honba.strategies.context import LedgerContext
from honba.strategies.runner import StrategyRunner


class SimulatedExchange:
    """Fake / simulated exchange that fills orders at the close of active bars."""

    def __init__(self) -> None:
        self.last_prices: dict[InstrumentId, float] = {}
        self.fills: list[Trade] = []
        self._next_ts = 0

    def on_event(self, event: object, ts: int) -> None:
        if isinstance(event, Bar):
            self.last_prices[event.instrument_id] = event.close

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        px = self.last_prices.get(intent.instrument_id)
        if px is None or px <= 0:
            return
        fill_ts = max(self._next_ts, ts)
        self._next_ts = fill_ts + 1
        self.fills.append(
            Trade(
                intent.instrument_id,
                intent.side,
                intent.quantity,
                px,
                fill_ts,
                order_id,
            )
        )

    def drain_fills(self) -> list[Trade]:
        f = self.fills
        self.fills = []
        return f


def run_session(
    strategy_cls: type,
    config: StrategyConfig,
    bars: list[Bar],
    initial_capital: float,
    label: str,
) -> dict:
    strat = strategy_cls(config)
    sim = SimulatedExchange()
    ctx = LedgerContext(cash=initial_capital)
    strat.bind(ctx)
    runner = StrategyRunner(strat, sim, ctx=ctx)

    events = [(b, b.ts) for b in bars]
    res = runner.run(events)

    final_cash = res.ctx.cash()
    pos_val = sum(
        qty * sim.last_prices.get(inst, 0.0)
        for inst, qty in res.ctx.positions().items()
        if qty > 0
    )
    final_equity = final_cash + pos_val
    ret_pct = ((final_equity - initial_capital) / initial_capital) * 100.0

    print("=" * 60)
    print(f"  {label}")
    print("=" * 60)
    print(f"Total Bars Processed : {len(bars):,}")
    print(f"Total Fills / Trades : {len(res.fills):,}")
    print(f"Initial Capital      : INR {initial_capital:,.2f}")
    print(f"Final Cash           : INR {final_cash:,.2f}")
    print(f"Open Positions Value : INR {pos_val:,.2f}")
    print(f"Total Equity         : INR {final_equity:,.2f}")
    print(f"Net Return           : {ret_pct:+.2f}%")
    print("=" * 60 + "\n")

    return {
        "label": label,
        "initial_capital": initial_capital,
        "final_cash": final_cash,
        "positions_value": pos_val,
        "final_equity": final_equity,
        "total_return_pct": ret_pct,
        "fills_count": len(res.fills),
    }


def main() -> None:
    # 1. Resolve Universe
    universe_name = "nifty200_alpha_30"
    universe = resolve_universe(universe_name, venue="NSE")
    print(f"[Universe] Resolved '{universe_name}': {len(universe)} equities")

    # 2. Configure Data Fetcher / Parquet Store
    store = ParquetBarStore()

    # 3. Load In-Sample Training Data: 2022-01-01 to 2025-12-30
    train_interval = DateInterval(dt.date(2022, 1, 1), dt.date(2025, 12, 31))
    train_bars: list[Bar] = []
    for inst in universe:
        train_bars.extend(store.read(inst, "1D", train_interval))
    train_bars.sort(key=lambda x: (x.ts, x.instrument_id.symbol))

    # 4. Load Out-of-Sample Backtesting Data: 2026-01-01 to today
    today = dt.date.today()
    test_interval = DateInterval(dt.date(2026, 1, 1), today + dt.timedelta(days=1))
    test_bars: list[Bar] = []
    for inst in universe:
        test_bars.extend(store.read(inst, "1D", test_interval))
    test_bars.sort(key=lambda x: (x.ts, x.instrument_id.symbol))

    # 5. Load Strategy
    import importlib.util
    strategy_path = (
        Path(__file__).resolve().parent.parent.parent
        / "honba-strategies"
        / "alpha_universe"
        / "alpha30_factor"
        / "strategy.py"
    )
    spec = importlib.util.spec_from_file_location("strategy", strategy_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load strategy from {strategy_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    strategy_cls = getattr(mod, "Alpha30Factor")

    config_path = strategy_path.parent / "config.toml"
    cfg = StrategyConfig.from_toml(config_path)

    # 6. Run Training / In-Sample
    run_session(
        strategy_cls,
        cfg,
        train_bars,
        initial_capital=cfg.params.get("capital", 1_000_000.0),
        label="Training / In-Sample (2022-01-01 to 2025-12-30)",
    )

    # 7. Run Backtesting / Out-of-Sample
    run_session(
        strategy_cls,
        cfg,
        test_bars,
        initial_capital=cfg.params.get("capital", 1_000_000.0),
        label=f"Backtesting / Out-of-Sample (2026-01-01 to {today})",
    )


if __name__ == "__main__":
    main()
