# 05 Strategies – reference strategies on synthetic data

Every example here runs with ``honba.strategies.testing.replay`` — no adapter,
no Parquet store, no network. The bars are synthetic (``tests/synthetic.py``),
the results are deterministic, and the same strategy class can later be plugged
into a real backtest or live runner unchanged.

## At a glance

| #  | File                             | Strategy class        | What you learn                                                          |
|----|----------------------------------|-----------------------|-------------------------------------------------------------------------|
| 01 | `01_first_strategy.py`           | `SmaCrossover`        | Minimal SMA crossover with the ``Sma`` indicator                        |
| 02 | `02_with_indicators.py`          | `CompositeSmaRsi`     | Composite: SMA + RSI filter + ATR sizing + trailing stop                |
| 03 | `03_position_sizing.py`          | `SmaSizing`           | Compare fixed / volatility / Kelly sizing on the same signals           |
| 04 | `04_risk_management.py`          | `RiskManagedSma`      | Portfolio drawdown, daily loss, and position limits as strategy guards  |
| 05 | `05_multi_instrument.py`         | `BasketSma`           | Basket of symbols with scheduled equal-weight rebalancing               |
| 06 | `06_equal_weight_rebalance.py`   | `PortfolioStrategy`   | Equal-weight rebalance composed from universe, weighting and schedule parts with ``PortfolioStrategy`` (Alpha 30 is just a universe name; compare with the catalog `portfolio/rebalancing/equal_weight`) |
| 07 | `07_declarative_long_short.py`   | `LongShortCrossover`  | Jesse-style ``DeclarativeStrategy``: long and short SMA crossover with stop and target |

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

## Declarative style

Three base classes in ``honba.strategies`` replace the ``on_bar`` body with declarations
(examples 07 and 06). ``DeclarativeStrategy`` is Jesse-style and per instrument: override
``should_long`` / ``should_short`` / ``should_exit`` and return ``Entry(quantity, stop_loss,
take_profit)`` from ``go_long`` / ``go_short``; the framework sends the entry and the
protective orders after the fill. ``TargetWeightStrategy`` is portfolio level: declare
``universe()`` (and optionally ``target_weights()``) and the framework values the portfolio
and rebalances. ``PortfolioStrategy`` (a ``TargetWeightStrategy``) is the composition layer on
top: you pass the parts, ``Universe -> Selector -> WeightingScheme -> RebalanceSchedule``
(``NamedUniverse``, ``TopN``, ``EqualWeight`` / ``InverseVolatility``, ``EveryNDays`` /
``MonthlyFirstSession`` / ``DriftBand``), either as objects or as TOML-friendly params through
``build_portfolio_strategy`` / ``PortfolioStrategy.from_params``. Example 06 shows it; a
named index such as Alpha 30 is just the ``universe`` argument, not a strategy of its own. All
three are plain ``Strategy`` subclasses, so they run unchanged in replay, backtest, paper and
live.

## Running them

```bash
python strategies/01_first_strategy.py
python strategies/02_with_indicators.py --rsi-period 14
python strategies/03_position_sizing.py --sizing volatility
python strategies/04_risk_management.py --max-drawdown-pct 0.15
python strategies/05_multi_instrument.py --rebalance-every 15
python strategies/06_equal_weight_rebalance.py --rebalance-days 10
python strategies/06_equal_weight_rebalance.py --weighting inverse_vol --schedule monthly:first_session
python strategies/07_declarative_long_short.py --stop-pct 0.02
pytest tests/unit -q -k "first_strategy or with_indicators or position_sizing or risk_management or multi_instrument or equal_weight_rebalance or declarative"
```

## From here to production

The same ``Strategy`` subclass works with:
- ``honba.strategies.testing.replay`` (these examples)
- ``honba.session.BacktestSession`` (event-driven backtest with Parquet data)
- ``honba.engine`` + adapter (paper/live)

The runner handles context binding, intent draining, fill application — the
strategy only sees ``self.ctx`` and emits intents.