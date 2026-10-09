# honba-examples

Runnable learning-path examples for [Honba](../honba). Every numbered script is
offline and deterministic by default — the `fake` adapter plus synthetic bars —
and each one teaches a single concept end to end: broker adapters, bars,
storage, universes, strategies, backtesting, mutual funds, options, AI research
and production.

## Setup

The examples import `honba` (the core repo) and a small shared helper package,
`honba_examples`, that lives in this repo.

```bash
pip install -e ../honba/python      # core (or however you install honba)
pip install -e ".[dev]"             # this repo: honba_examples + pytest/ruff
```

Scripts also run from a plain checkout without the editable install: those that
need `honba_examples` add the repo root to `sys.path` when the package is not
installed.

Some examples load strategies from the sibling `honba-strategies` catalog. They
look for it at `--strategies-dir`, then `$HONBA_STRATEGIES_DIR`, then
`../honba-strategies` next to this repo.

```bash
pytest -q                 # unit + integration tests (no network, synthetic data)
ruff check . && ruff format --check honba_examples tests
```

## Examples and their strategies

Most examples are about the plumbing (adapters, bars, storage); the rest run a
strategy. The tables below index both: for every script, the strategy it runs and
the indicators, sizing or portfolio parts it builds on.

Strategies come in two flavours:

- **Inline demo classes** defined in the example itself (e.g. `SmaCrossover`),
  kept small so the lesson is visible in one file.
- **Catalog strategies** loaded by registry name from `honba-strategies`
  (e.g. `equal_weight`), the same code that ships to users.

### `basic/` — broker adapters

Adapter mechanics only, no trading strategy. See also the
[adapter examples](../honba-adapters/examples/).

| # | File | Shows |
|---|---|---|
| 01 | `01_connect_dhan.py` | Connect through the registry; print session and capabilities |
| 02 | `02_fetch_instruments.py` | Pull the instrument master (lot size, tick size) |
| 03 | `03_subscribe_quotes.py` | Stream live quotes and compare with snapshots |
| 04 | `04_place_order_paper.py` | Intent → report → fills, a cancel, a reject, the books |
| 05 | `05_check_positions.py` | Positions, funds and trades after a round trip |

Run them:

```bash
python basic/01_connect_dhan.py
python basic/02_fetch_instruments.py --query RELIANCE
python basic/03_subscribe_quotes.py --advances 5
python basic/04_place_order_paper.py --out orders.json
python basic/05_check_positions.py
```

### `candles/` — bar construction

No strategy; how bars are fetched and folded.

| # | File | Shows |
|---|---|---|
| 01 | `01_historical_candles.py` | `historical_bars` over a half-open `[start, end)` |
| 02 | `02_realtime_candles.py` | Build OHLC frames from pushed `QuoteTick`s |
| 03 | `03_custom_aggregation.py` | The OHLC fold (`honba_examples/bars.py`) |
| 04 | `04_multi_timeframe.py` | One fetch, many resolutions, alignment invariants |

Run them:

```bash
python candles/01_historical_candles.py --timeframe 1m --instrument RELIANCE
python candles/02_realtime_candles.py --advances 5
python candles/03_custom_aggregation.py --timeframe 5m --out bars.json
python candles/04_multi_timeframe.py
```

### `storage/` — persistence

| # | File | Strategy / purpose |
|---|---|---|
| 01 | `01_parquet_catalog.py` | No strategy — `ParquetBarStore` layout and read/write contract |
| 02 | `02_import_nse_bhavcopy.py` | No strategy — parse an exchange EOD CSV into bars |
| 03 | `03_import_amfi_nav.py` | No strategy — import AMFI `NAVAll.txt` into the store |
| 04 | `04_export_tearsheet.py` | Inline `BuyFirstSellLast` — buy the first test session, exit before the last |

Run them:

```bash
python storage/01_parquet_catalog.py --data-dir /tmp/store
python storage/02_import_nse_bhavcopy.py --data-dir /tmp/bhavcopy
python storage/03_import_amfi_nav.py --data-dir /tmp/nav
python storage/04_export_tearsheet.py --out-dir /tmp/tearsheet
```

### `universes/` — universes and the Alpha 30 equal-weight strategy

Alpha 30 is a **universe** (a parameter); the strategy is "equal-weight periodic
rebalance". The catalog entry is `portfolio/rebalancing/equal_weight`.

| # | File | Strategy / purpose |
|---|---|---|
| 01 | `01_nifty50_constituents.py` | No strategy — `resolve_universe("nifty50")` |
| 02 | `02_banknifty_constituents.py` | No strategy — thematic universe, same API |
| 03 | `03_alpha30_constituents.py` | No strategy — `load_alpha30()` seed/helper (`alpha30_constituents.py` alias) |
| 04 | `04_alpha30_generic_rebalancer.py` | Inline `UniverseEqualWeightRebalance` (any universe) |
| 05 | `05_alpha30_custom_universe.py` | Registers a custom universe; reuses `UniverseEqualWeightRebalance` |
| 06 | `06_alpha30_equal_weight_demo.py` | Alpha 30 specialization of generic rebalancer 04 |
| 07 | `07_alpha30_backtest.py` | Catalog `equal_weight` on universe `nifty200_alpha_30`, next-open fills, T+N settlement |
| 08 | `08_alpha30_union_ewr_backtest.py` | Hand-rolled equal-weight rebalancer (close fills, flat fee) |

Run them:

```bash
python universes/01_nifty50_constituents.py
python universes/04_alpha30_generic_rebalancer.py
python universes/06_alpha30_equal_weight_demo.py
python universes/07_alpha30_backtest.py --test-start 2026-06-01 --out-dir /tmp/alpha30
python universes/08_alpha30_union_ewr_backtest.py --start 2026-01-01 --end 2026-09-23
```

### `strategies/` — reference strategies

Runs on synthetic data via `honba.strategies.testing.replay`.

| # | File | Strategy class | Indicators / parts |
|---|---|---|---|
| 01 | `01_first_strategy.py` | `SmaCrossover` | SMA (fast/slow) |
| 02 | `02_with_indicators.py` | `CompositeSmaRsi` | SMA + RSI filter + ATR sizing + trailing stop |
| 03 | `03_position_sizing.py` | `SmaSizing` | SMA + ATR; fixed / volatility / Kelly sizing |
| 04 | `04_risk_management.py` | `RiskManagedSma` | SMA + drawdown, daily-loss and position guards |
| 05 | `05_multi_instrument.py` | `BasketSma` | SMA per symbol + equal-weight rebalance |
| 06 | `06_equal_weight_rebalance.py` | `PortfolioStrategy` | `NamedUniverse` + `EqualWeight`/`InverseVolatility` + `EveryNDays`/`MonthlyFirstSession`/`DriftBand`; matches catalog `equal_weight` |
| 07 | `07_declarative_long_short.py` | `LongShortCrossover` | `DeclarativeStrategy`; long/short SMA crossover + stop and target |

Run them:

```bash
python strategies/01_first_strategy.py
python strategies/02_with_indicators.py --rsi-period 14
python strategies/03_position_sizing.py --sizing volatility
python strategies/04_risk_management.py --max-drawdown-pct 0.15
python strategies/05_multi_instrument.py --rebalance-every 15
python strategies/06_equal_weight_rebalance.py --rebalance-days 10
python strategies/07_declarative_long_short.py --stop-pct 0.02
```

### `backtesting/` — end-to-end runs

All runs use an SMA crossover demo class on the event-driven harness with the
India cost model.

| # | File | Strategy class | Adds |
|---|---|---|---|
| 01 | `01_first_backtest.py` | `SmaBacktest` | Next-open fills, NSE equity costs |
| 02 | `02_cost_modeling.py` | — | Per-leg cost breakdown (brokerage, STT, exchange, SEBI, IPFT, stamp, GST) |
| 03 | `03_latency_modeling.py` | `SmaLatency` | Fill models: bar_close / next_open / next_close |
| 04 | `04_walk_forward.py` | `SmaWalk` | Rolling in-sample → out-of-sample windows |
| 05 | `05_monte_carlo.py` | `SmaMonteCarlo` | Bootstrap equity curves (final equity, DD, Sharpe) |
| 06 | `06_concurrent_backtest.py` | `SmaSweep` | Thread-pool parameter sweep, Pareto frontier |

Run them:

```bash
python backtesting/01_first_backtest.py --out first_backtest.json
python backtesting/02_cost_modeling.py --price 2500 --qty 10
python backtesting/03_latency_modeling.py
python backtesting/04_walk_forward.py --bars 120 --window 40 --step 20
python backtesting/05_monte_carlo.py --paths 1000
python backtesting/06_concurrent_backtest.py --workers 4
```

### `mutual_funds/` — NAV and SIP

No `Strategy` subclasses; direct NAV/SIP math over `honba_examples.amfi`.

| # | File | Purpose |
|---|---|---|
| 01 | `01_fetch_nav.py` | AMFI NAV history; resample to daily; handle missing days |
| 02 | `02_sip_backtest.py` | Fixed-date monthly SIP, units at NAV, XIRR |
| 03 | `03_category_analysis.py` | Category stats, rolling returns, quartile ranking |
| 04 | `04_portfolio_optimization.py` | Mean-variance allocation with constraints |

Run them:

```bash
python mutual_funds/01_fetch_nav.py --scheme 120503 --start 2023-01-01 --end 2024-12-31
python mutual_funds/02_sip_backtest.py --scheme 120503 --monthly 10000 --start 2023-01-01
python mutual_funds/03_category_analysis.py --category "Equity:Large Cap" --start 2020-01-01
python mutual_funds/04_portfolio_optimization.py --schemes 120503,120504,120505 --start 2022-01-01
```

### `options/` — chain, Greeks and strategy backtests

Hand-rolled legs (no `Strategy` subclass); see the catalog options strategies
(`options/nifty_short_straddle`, `options/banknifty_iron_condor`,
`options/expiry_day_scalp`) for the packaged versions.

| # | File | Strategy |
|---|---|---|
| 01 | `01_option_chain.py` | No strategy — chain snapshot, filter by expiry/strike/moneyness |
| 02 | `02_greeks_calculation.py` | No strategy — Black-Scholes Greeks per contract |
| 03 | `03_backtest_straddle.py` | Long ATM straddle, hold to expiry |
| 04 | `04_backtest_iron_condor.py` | Iron condor (4-leg credit spread) |
| 05 | `05_expiry_day_strategy.py` | Expiry-day gamma scalping / theta capture |

Run them:

```bash
python options/01_option_chain.py --underlying NIFTY --expiry 2024-12-26
python options/02_greeks_calculation.py --spot 24000 --strike 24000 --days-to-expiry 7
python options/03_backtest_straddle.py --underlying NIFTY --expiry 2024-12-26
python options/04_backtest_iron_condor.py --underlying NIFTY --expiry 2024-12-26
python options/05_expiry_day_strategy.py --underlying BANKNIFTY --expiry 2024-12-25
```

### `ai_research/` — the research loop

No trading strategy; 01 and 05 are runnable, the rest are design placeholders.

| # | File | Status | Purpose |
|---|---|---|---|
| 01 | `01_llm_research_analyst.py` | Runnable | NL question → validated screener filter |
| 02 | `02_autoresearch_loop.py` | Design only | Hypothesis → backtest → journal loop |
| 03 | `03_nl_to_strategy.py` | Design only | NL → strategy manifest → `verify_manifest` |
| 04 | `04_rl_training.py` | Design only | Gym environment + RL policy |
| 05 | `05_mcp_integration.py` | Runnable | Enumerate the static MCP tool schema |
| 06 | `06_natural_language_critique.py` | Design only | Backtest result → structured LLM critique |

Run one (the two runnable steps):

```bash
python ai_research/01_llm_research_analyst.py --query "mid cap with rsi below 30"
python ai_research/05_mcp_integration.py
```

### `production/` — paper, live, monitoring, deployment

All run the `sma_crossover` strategy; swap `--adapter dhan` (or any registered
adapter) for real connectivity.

| # | File | Strategy |
|---|---|---|
| 01 | `01_paper_trading.py` | Inline `SmaPaper` — simulated fills, P&L, risk guards |
| 02 | `02_live_trading_dhan.py` | Inline `SmaLive` — Dhan connect, orders, rejections, position sync |
| 03 | `03_multi_account.py` | Inline `SmaMulti` — isolated capital, aggregated P&L |
| 04 | `04_monitoring.py` | No strategy — adapter latency, order throughput, position drift |
| 05 | `05_deployment.py` | No strategy — systemd/container config for `sma_crossover` |

Run them:

```bash
python production/01_paper_trading.py --strategy sma_crossover --capital 100000
python production/02_live_trading_dhan.py --client-id XXX --access-token YYY  # needs Dhan creds
python production/03_multi_account.py --accounts config/accounts.yaml
python production/04_monitoring.py --port 9090
python production/05_deployment.py --output /etc/honba/
```

All examples default to the `fake` adapter, so they run with no credentials. Swap
`--adapter dhan` (or any registered adapter) with real credentials for live broker
connectivity.
