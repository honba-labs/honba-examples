# 06 Backtesting – end-to-end runs

Every example here uses Honba's event-driven backtest harness with synthetic data.
No Parquet store, no network — just the runner and the cost model.

## At a glance

| #  | File                            | Strategy class    | What you learn                                                           |
|----|---------------------------------|-------------------|--------------------------------------------------------------------------|
| 01 | `01_first_backtest.py`          | `SmaBacktest`     | Minimal backtest: SMA crossover, next-open fills, India equity costs     |
| 02 | `02_cost_modeling.py`           | —                 | Per-leg cost breakdown (brokerage, STT, exchange, SEBI, IPFT, stamp, GST) |
| 03 | `03_latency_modeling.py`        | `SmaLatency`      | Compare fill models: bar_close / next_open / next_close                  |
| 04 | `04_walk_forward.py`            | `SmaWalk`         | Rolling window optimization/validation (in-sample → out-of-sample)       |
| 05 | `05_monte_carlo.py`             | `SmaMonteCarlo`   | Bootstrap equity curves for risk distribution (final equity, DD, Sharpe) |
| 06 | `06_concurrent_backtest.py`     | `SmaSweep`        | Parameter sweep with thread pool; Pareto frontier & best-by-Sharpe       |

## The common pattern

```python
from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma
from honba.strategies.testing import replay
from honba_examples.metrics import curve_metrics

class MyStrategy(Strategy):
    name = "my_strategy"
    warmup_bars = 30

    def __init__(self, fast=10, slow=30):
        super().__init__()
        self.fast = Sma(fast)
        self.slow = Sma(slow)

    def on_bar(self, bar):
        fast_val = self.fast.update(bar.close)
        slow_val = self.slow.update(bar.close)
        if fast_val is None or slow_val is None:
            return
        position = self.position(bar.instrument_id)
        if fast_val > slow_val and position <= 0:
            self.buy(bar.instrument_id, 1)
        elif fast_val < slow_val and position >= 0 and position > 0:
            self.sell(bar.instrument_id, position)

# Run with synthetic bars
from tests.synthetic import bar as synth_bar, weekdays
bars = [synth_bar("RELIANCE", d, 2500.0 + i * 0.5) for i, d in enumerate(weekdays(dt.date(2026, 1, 1), 60))]
result = replay(MyStrategy(), bars)
```

## Running them

```bash
python backtesting/01_first_backtest.py --out first_backtest.json
python backtesting/02_cost_modeling.py --price 2500 --qty 10
python backtesting/03_latency_modeling.py
python backtesting/04_walk_forward.py --bars 120 --window 40 --step 20
python backtesting/05_monte_carlo.py --paths 1000
python backtesting/06_concurrent_backtest.py --workers 4
pytest tests/unit -q -k "first_backtest or cost_modeling or latency_modeling or walk_forward or monte_carlo or concurrent_backtest"
```

## Key concepts demonstrated

1. **Synthetic data** — `tests/synthetic.py` provides deterministic bar generators; same bars = same results
2. **Cost model** — `honba.markets.india.costs.nse_equity_delivery_*` applies the full per-leg breakdown
3. **Fill models** — `fill_delay` in `replay()` controls bar_close (0), next_open (1), next_close (2), etc.
4. **Walk-forward** — Optimize on train window, validate on test window, roll forward
5. **Monte Carlo** — Resample fills with replacement to estimate distribution of outcomes
6. **Concurrent sweep** — `ThreadPoolExecutor` over parameter grid; deterministic ordering preserved

## From here to production backtests

The same `Strategy` subclass works with:
- `honba.strategies.testing.replay` (these examples — bar-close fills, no settlement)
- `honba.session.BacktestSession` (event-driven with Parquet data, next-open fills, T+N settlement)
- `honba.engine` + adapter (paper/live)

The harness handles context binding, intent draining, fill application, settlement — the strategy only sees `self.ctx` and emits intents.