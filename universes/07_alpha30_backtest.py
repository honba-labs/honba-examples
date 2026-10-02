"""Example: Run backtest and training for Alpha 30 Factor Strategy.

Configures data-fetcher / Parquet store, gets data for 30 equities from NSE NIFTY200 ALPHA 30,
runs in-sample training (2022-01-01 to 2025-12-30) and out-of-sample backtesting (2026-01-01 to today).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
import math
from collections import defaultdict
from typing import Any

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
from honba.domain.order import OrderSide  # or entities.order





# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------
def _ts_to_date(ts: int) -> dt.date:
    """Unix-ns → calendar date (UTC)."""
    return dt.datetime.fromtimestamp(ts / 1e9, tz=dt.timezone.utc).date()


def _max_drawdown(equity: list[float]) -> float:
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd * 100.0  # percent


def _sharpe(daily_returns: list[float], rf: float = 0.0) -> float:
    if len(daily_returns) < 2:
        return 0.0
    mean = sum(daily_returns) / len(daily_returns)
    var = sum((r - mean) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std = math.sqrt(var)
    if std < 1e-12:
        return 0.0
    # annualise (252 trading days)
    return ((mean - rf / 252) / std) * math.sqrt(252)


def _cagr(initial: float, final: float, n_days: int) -> float:
    if initial <= 0 or n_days <= 0:
        return 0.0
    years = n_days / 365.25
    return ((final / initial) ** (1 / years) - 1) * 100.0


def _turnover(fills: list[Trade], avg_equity: float) -> float:
    """Sum of absolute traded notional / average equity."""
    if avg_equity <= 0:
        return 0.0
    notional = sum(abs(f.quantity * f.price) for f in fills)
    return notional / avg_equity


"""NSE cash-equity delivery costs (Zerodha-style / discount broker).

Rates as of 2025-26 (delivery / CNC):
  STT            0.10 %  on sell notional only
  Stamp duty     0.015%  on buy notional only
  Exchange txn   0.00297% both sides (NSE)
  SEBI charges   0.0001% both sides
  IPFT           0.0001% both sides (approx)
  Brokerage      min(0.03% of notional, ₹20) both sides
  GST            18% on (brokerage + exchange + SEBI + IPFT)
"""


# ---------------------------------------------------------------------------
# Simulated exchange (unchanged)
# ---------------------------------------------------------------------------
from honba.entities.order import OrderIntent, OrderSide
from honba.entities.trade import Trade
from honba.markets.india.costs import nse_equity_delivery_cost

class SimulatedExchange:
    def __init__(self) -> None:
        self.last_prices: dict[InstrumentId, float] = {}
        self.fills: list[Trade] = []
        self._next_ts = 0
        self.total_fees: float = 0.0          # convenience accumulator

    def on_event(self, event: object, ts: int) -> None:
        if isinstance(event, Bar):
            self.last_prices[event.instrument_id] = event.close

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        px = self.last_prices.get(intent.instrument_id)
        if px is None or px <= 0:
            return
        fill_ts = max(self._next_ts, ts)
        self._next_ts = fill_ts + 1

        cost = nse_equity_delivery_cost(intent.side, intent.quantity, px)
        self.total_fees += cost

        self.fills.append(
            Trade(
                intent.instrument_id,
                intent.side,
                intent.quantity,
                px,
                fill_ts,
                order_id,
                costs=cost,          # ← LedgerContext.apply_fill will debit this
            )
        )

    def drain_fills(self) -> list[Trade]:
        f, self.fills = self.fills, []
        return f


# ---------------------------------------------------------------------------
# Session runner with full metrics
# ---------------------------------------------------------------------------
def run_session(
    strategy_cls: type,
    config: StrategyConfig,
    bars: list[Bar],
    initial_capital: float,
    label: str,
) -> dict[str, Any]:
    strat = strategy_cls(config)
    sim = SimulatedExchange()
    ctx = LedgerContext(cash=initial_capital)
    strat.bind(ctx)
    runner = StrategyRunner(strat, sim, ctx=ctx)

    # --- daily equity curve ---
    equity_by_day: dict[dt.date, float] = {}
    cash_by_day: dict[dt.date, float] = {}
    last_day: dt.date | None = None

    events = [(b, b.ts) for b in bars]
    # Manual event loop so we can snapshot equity at each day-end
    runner.start()
    for event, ts in events:
        # let the sim see the bar first (same order as StrategyRunner.run)
        if hasattr(sim, "on_event"):
            sim.on_event(event, ts)
        runner.on_event(event, ts)

        day = _ts_to_date(ts)
        if last_day is not None and day != last_day:
            # previous day is complete – record its closing equity
            pos_val = sum(
                qty * sim.last_prices.get(iid, 0.0)
                for iid, qty in ctx.positions().items()
                if qty != 0
            )
            equity_by_day[last_day] = ctx.cash() + pos_val
            cash_by_day[last_day] = ctx.cash()
        last_day = day

    runner.stop()

    # final day
    if last_day is not None:
        pos_val = sum(
            qty * sim.last_prices.get(iid, 0.0)
            for iid, qty in ctx.positions().items()
            if qty != 0
        )
        equity_by_day[last_day] = ctx.cash() + pos_val
        cash_by_day[last_day] = ctx.cash()

    # --- derive metrics ---
    days = sorted(equity_by_day)
    equity = [equity_by_day[d] for d in days]
    cash_series = [cash_by_day[d] for d in days]

    final_equity = equity[-1] if equity else initial_capital
    final_cash = cash_series[-1] if cash_series else initial_capital
    pos_val = final_equity - final_cash
    ret_pct = ((final_equity - initial_capital) / initial_capital) * 100.0

    daily_rets = [
        (equity[i] - equity[i - 1]) / equity[i - 1]
        for i in range(1, len(equity))
        if equity[i - 1] > 0
    ]
    n_calendar_days = (days[-1] - days[0]).days if len(days) > 1 else 1
    avg_equity = sum(equity) / len(equity) if equity else initial_capital
    avg_cash_pct = (
        (sum(cash_series) / len(cash_series)) / avg_equity * 100.0 if avg_equity > 0 else 0.0
    )

    max_dd = _max_drawdown(equity)
    sharpe = _sharpe(daily_rets)
    cagr = _cagr(initial_capital, final_equity, n_calendar_days)
    turnover = _turnover(res.fills if (res := type("R", (), {"fills": runner.fills})()) else [], avg_equity)
    # runner.fills is populated by StrategyRunner
    fills = runner.fills
    turnover = _turnover(fills, avg_equity)
    total_fees = sum(getattr(f, "costs", 0.0) for f in fills)

    # --- print Jesse-style table ---
    print("=" * 60)
    print(f"  {label}")
    print("=" * 60)
    print(f"Total Bars Processed : {len(bars):,}")
    print(f"Total Fills / Trades : {len(fills):,}")
    print(f"Initial Capital      : INR {initial_capital:,.2f}")
    print(f"Final Cash           : INR {final_cash:,.2f}")
    print(f"Open Positions Value : INR {pos_val:,.2f}")
    print(f"Total Equity         : INR {final_equity:,.2f}")
    print(f"Net Return           : {ret_pct:+.2f}%")
    print(f"CAGR                 : {cagr:+.2f}%")
    print(f"Max Drawdown         : {max_dd:.2f}%")
    print(f"Sharpe (ann.)        : {sharpe:.2f}")
    print(f"Turnover             : {turnover:.2f}x")
    print(f"Avg Cash             : {avg_cash_pct:.1f}%")
    print(f"Total Fees           : INR {total_fees:,.2f}")
    print("=" * 60 + "\n")

    return {
        "label": label,
        "initial_capital": initial_capital,
        "final_cash": final_cash,
        "positions_value": pos_val,
        "final_equity": final_equity,
        "total_return_pct": ret_pct,
        "cagr_pct": cagr,
        "max_drawdown_pct": max_dd,
        "sharpe": sharpe,
        "turnover": turnover,
        "avg_cash_pct": avg_cash_pct,
        "total_fees": total_fees,
        "fills_count": len(fills),
        "equity_curve": [{"date": d.isoformat(), "value": equity_by_day[d], "cash": cash_by_day[d]} for d in days],
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
        / "alpha30_equal_weight"      # ← correct directory
        / "strategy.py"
    )
    spec = importlib.util.spec_from_file_location("strategy", strategy_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load strategy from {strategy_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    strategy_cls = getattr(mod, "Alpha30EqualWeight")

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
