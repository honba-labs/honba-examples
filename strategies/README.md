# 05 Strategies – reference strategies on synthetic data

Every example here runs with ``honba.strategies.testing.replay`` — no adapter,
no Parquet store, no network. The bars are synthetic (``tests/synthetic.py``),
the results are deterministic, and the same strategy class can later be plugged
into a real backtest or live runner unchanged.

| #  | File                             | What you learn                                                          |
|----|----------------------------------|-------------------------------------------------------------------------|
| 01 | `01_first_strategy.py`           | Minimal SMA crossover with the ``Sma`` indicator                        |
| 02 | `02_with_indicators.py`          | Composite: SMA + RSI filter + ATR sizing + trailing stop                |
| 03 | `03_position_sizing.py`          | Compare fixed / volatility / Kelly sizing on the same signals           |
| 04 | `04_risk_management.py`          | Portfolio drawdown, daily loss, and position limits as strategy guards  |
| 05 | `05_multi_instrument.py`         | Basket of symbols with scheduled equal-weight rebalancing               |

## The common pattern

```python
from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma, Rsi, Atr
from honba.strategies.testing import replay

class MyStrategy(Strategy):
    name = "my_strategy"
    warmup_bars = 30  # bars before orders are allowed

    def on_bar(self, bar):
        # read price, update indicators, submit intents via self.buy/sell
        ...

strat = MyStrategy(...)
result = replay(strat, bars)  # or: StrategyRunner + LedgerContext in a real run
```

## Running them

```bash
python strategies/01_first_strategy.py
python strategies/02_with_indicators.py --rsi-period 14
python strategies/03_position_sizing.py --sizing volatility
python strategies/04_risk_management.py --max-drawdown-pct 0.15
python strategies/05_multi_instrument.py --rebalance-every 15
pytest tests/unit -q -k "first_strategy or with_indicators or position_sizing or risk_management or multi_instrument"
```

## From here to production

The same ``Strategy`` subclass works with:
- ``honba.strategies.testing.replay`` (these examples)
- ``honba.session.BacktestSession`` (event-driven backtest with Parquet data)
- ``honba.engine`` + adapter (paper/live)

The runner handles context binding, intent draining, fill application — the
strategy only sees ``self.ctx`` and emits intents.