# Examples index

Runnable learning-path examples for [Honba](../../honba-tamashii). Every numbered script is
offline and deterministic by default — the `fake` adapter plus synthetic bars — and each one
teaches a single concept end to end.

Run every command below from the repo root, after the editable install described in the root
[README](../README.md#install).

Most examples are about the plumbing (adapters, bars, storage); the rest run a strategy. The
tables below index both: for every script, the strategy it runs and the indicators, sizing or
portfolio parts it builds on.

Strategies come in two flavours:

- **Inline demo classes** defined in the example itself (e.g. `SmaCrossover`), kept small so the
  lesson is visible in one file.
- **Catalog strategies** loaded by registry name from `honba-strategies` (e.g. `equal_weight`),
  the same code that ships to users.

## `basic/` — broker adapters

Adapter mechanics only, no trading strategy. See also the
[adapter examples](../../honba-adapters/examples/).

| # | File | Shows |
|---|---|---|
| 01 | `01_connect_dhan.py` | Connect through the registry; print session and capabilities |
| 02 | `02_fetch_instruments.py` | Pull the instrument master (lot size, tick size) |
| 03 | `03_subscribe_quotes.py` | Stream live quotes and compare with snapshots |
| 04 | `04_place_order_paper.py` | Intent → report → fills, a cancel, a reject, the books |
| 05 | `05_check_positions.py` | Positions, funds and trades after a round trip |

Run them:

```bash
python examples/basic/01_connect_dhan.py
python examples/basic/02_fetch_instruments.py --query RELIANCE
python examples/basic/03_subscribe_quotes.py --advances 5
python examples/basic/04_place_order_paper.py --out orders.json
python examples/basic/05_check_positions.py
```

## `candles/` — bar construction

No strategy; how bars are fetched and folded.

| # | File | Shows |
|---|---|---|
| 01 | `01_historical_candles.py` | `historical_bars` over a half-open `[start, end)` |
| 02 | `02_realtime_candles.py` | Build OHLC frames from pushed `QuoteTick`s |
| 03 | `03_custom_aggregation.py` | The OHLC fold (`honba_examples/bars.py`) |
| 04 | `04_multi_timeframe.py` | One fetch, many resolutions, alignment invariants |

Run them:

```bash
python examples/candles/01_historical_candles.py --timeframe 1m --instrument RELIANCE
python examples/candles/02_realtime_candles.py --advances 5
python examples/candles/03_custom_aggregation.py --timeframe 5m --out bars.json
python examples/candles/04_multi_timeframe.py
```

## `storage/` — persistence

| # | File | Strategy / purpose |
|---|---|---|
| 01 | `01_parquet_catalog.py` | No strategy — `ParquetBarStore` layout and read/write contract |
| 02 | `02_import_nse_bhavcopy.py` | No strategy — parse an exchange EOD CSV into bars |
| 03 | `03_import_amfi_nav.py` | No strategy — import AMFI `NAVAll.txt` into the store |
| 04 | `04_export_tearsheet.py` | Inline `BuyFirstSellLast` — buy the first test session, exit before the last |

Run them:

```bash
python examples/storage/01_parquet_catalog.py --data-dir /tmp/store
python examples/storage/02_import_nse_bhavcopy.py --data-dir /tmp/bhavcopy
python examples/storage/03_import_amfi_nav.py --data-dir /tmp/nav
python examples/storage/04_export_tearsheet.py --out-dir /tmp/tearsheet
```

## `universes/` — universes and the Alpha 30 equal-weight strategy

Alpha 30 is a **universe** (a parameter); the strategy is "equal-weight periodic
rebalance". The catalog entry is `portfolio/rebalancing/equal_weight`.

| # | File | Strategy / purpose |
|---|---|---|
| 01 | `01_nifty50_constituents.py` | No strategy — `resolve_universe("nifty50")` |
| 02 | `02_banknifty_constituents.py` | No strategy — thematic universe; exits with a message until a Bank Nifty history exists |
| 03 | `03_alpha30_constituents.py` | No strategy — `load_alpha30()` seed/helper (`alpha30_constituents.py` alias) |
| 04 | `04_alpha30_generic_rebalancer.py` | Inline `UniverseEqualWeightRebalance` (any universe) |
| 05 | `05_alpha30_custom_universe.py` | Registers a custom universe; reuses `UniverseEqualWeightRebalance` |
| 06 | `06_alpha30_equal_weight_demo.py` | Alpha 30 specialization of generic rebalancer 04 |
| 07 | `07_alpha30_backtest.py` | Catalog `equal_weight` on universe `nifty200_alpha_30`, next-open fills, T+N settlement |
| 08 | `08_alpha30_union_ewr_backtest.py` | Hand-rolled equal-weight rebalancer (close fills, flat fee) |
| 09 | `09_alpha30_cross_sectional.py` | Momentum-volatility factor scores; monthly top-decile rebalance (lakehouse Arrow read or synthetic bars) |

Run them:

```bash
python examples/universes/01_nifty50_constituents.py
python examples/universes/04_alpha30_generic_rebalancer.py
python examples/universes/06_alpha30_equal_weight_demo.py
python examples/universes/07_alpha30_backtest.py --test-start 2026-06-01 --out-dir /tmp/alpha30
python examples/universes/08_alpha30_union_ewr_backtest.py --start 2026-01-01 --end 2026-09-23
```

## `strategies/` — reference strategies

Runs on synthetic data via `honba.strategies.testing.replay`.

| # | File | Strategy class | Indicators / parts |
|---|---|---|---|
| 01 | `01_first_strategy.py` | `SmaCrossover` | SMA (fast/slow) |
| 02 | `02_with_indicators.py` | `CompositeSmaRsi` | SMA/EMA + RSI filter + ATR sizing + trailing stop |
| 03 | `03_position_sizing.py` | `SmaSizing` | SMA + ATR; fixed / volatility / fractional-Kelly sizing (capped by equity) |
| 04 | `04_risk_management.py` | `RiskManagedSma` | SMA + drawdown, daily-loss and position guards |
| 05 | `05_multi_instrument.py` | `BasketSma` | SMA per symbol + equal-weight rebalance |
| 06 | `06_equal_weight_rebalance.py` | `PortfolioStrategy` | `NamedUniverse` + `EqualWeight`/`InverseVolatility` + `EveryNDays`/`MonthlyFirstSession`/`DriftBand`; matches catalog `equal_weight` |
| 07 | `07_declarative_long_short.py` | `LongShortCrossover` | `DeclarativeStrategy`; long/short SMA crossover + stop and target |
| 12 | `12_buy_and_hold_lifecycle.py` | `BuyAndHold` | Canonical lifecycle hooks: `on_start`, `on_bar`, and `on_stop` |
| 13 | `13_bracket_order_mis.py` | `BracketOrderMis` | Take-profit / stop-loss bracket orders + MIS intraday margin with 15:20 IST auto-square-off |
| 14 | `14_partial_fill_handling.py` | `PartialFillStrategy` | Order lifecycle events (`on_order_event`), partial fill accounting, order modification |
| 15 | `15_multi_symbol_portfolio.py` | `MultiSymbolPortfolio` | Multi-symbol portfolio across 5 NIFTY scrips with per-symbol sizing & risk caps |
| 16 | `16_dynamic_position_sizing.py` | `DynamicPositionSizing` | First-class comparison: fixed lot vs fixed fractional (1% risk) vs ATR volatility targeting |

Run them:

```bash
python examples/strategies/01_first_strategy.py
python examples/strategies/02_with_indicators.py --rsi-period 14 --use-ema
python examples/strategies/03_position_sizing.py --sizing volatility
python examples/strategies/04_risk_management.py --max-drawdown-pct 0.15
python examples/strategies/05_multi_instrument.py --rebalance-every 15
python examples/strategies/06_equal_weight_rebalance.py --rebalance-days 10
python examples/strategies/07_declarative_long_short.py --stop-pct 0.02
python examples/strategies/12_buy_and_hold_lifecycle.py
python examples/strategies/13_bracket_order_mis.py --take-profit-pct 0.04 --stop-loss-pct 0.02
python examples/strategies/14_partial_fill_handling.py --target-qty 20
python examples/strategies/15_multi_symbol_portfolio.py --max-weight 0.20
python examples/strategies/16_dynamic_position_sizing.py --compare
```

## `backtesting/` — end-to-end runs

All runs use an SMA crossover demo class on the event-driven harness with the
India cost model.

| # | File | Strategy class | Adds |
|---|---|---|---|
| 01 | `01_first_backtest.py` | `SmaBacktest` | Next-open fills (`fill_delay=1`), `--capital` sizing, names its data source; gross of costs |
| 02 | `02_cost_modeling.py` | — | Per-leg cost breakdown (brokerage, STT, exchange, SEBI, IPFT, stamp, GST) |
| 03 | `03_latency_modeling.py` | `SmaLatency` | Fill models: bar_close / next_open / open_after_2_bars (latency stress) |
| 04 | `04_walk_forward.py` | `SmaWalk` | Rolling in-sample → out-of-sample windows, scored by net return |
| 05 | `05_monte_carlo.py` | `SmaMonteCarlo` | Bootstrap closed round-trip trades (final equity, DD, Sharpe); needs enough trades |
| 06 | `06_concurrent_backtest.py` | `SmaSweep` | Thread-pool parameter sweep, Pareto frontier, multiple-testing count |
| 07 | `07_run_via_client.py` | — | Same strategy over in-process and HTTP transports; run_id and assumptions |
| 08 | `08_sip_buy_and_hold.py` | — | Systematic investment plan (SIP): monthly/weekly buy-and-hold, XIRR, DD |
| 09 | `09_forward_sip_backtest.py` | — | Forward SIP backtester (``--symbols`` or stdin pipe); alias of 10 |
| 10 | `10_chained_screener_and_sip.py` | — | Chained screener (momentum, value, dividend) & forward SIP out-of-sample backtest |
| 11 | `11_portfolio_rebalancer.py` | — | Equal-weight periodic rebalance via the `ewr` simulator (flat fee, T+N; ``--symbols``/stdin-aware) |

Run them:

```bash
python examples/backtesting/01_first_backtest.py --out first_backtest.json
python examples/backtesting/02_cost_modeling.py --price 2500 --qty 10
python examples/backtesting/03_latency_modeling.py
python examples/backtesting/04_walk_forward.py --synthetic --train-months 12 --test-months 3 --n-folds 4
python examples/backtesting/05_monte_carlo.py --synthetic --offsets 0 5 10 20
python examples/backtesting/06_concurrent_backtest.py --workers 4
python examples/backtesting/08_sip_buy_and_hold.py --symbol ALPHAETF --frequency monthly --sip-amount 5000
python examples/backtesting/10_chained_screener_and_sip.py --strategy multibagger --asof 2023-01-01 --top 5
python examples/backtesting/10_chained_screener_and_sip.py --strategy value --asof 2023-01-01 --top 5
python examples/backtesting/10_chained_screener_and_sip.py --strategy multibagger --screen-only --top 5
```

## `optimizer/` — parameter search & overfitting diagnostics

| # | File | Shows |
|---|---|---|
| 01 | `01_ga_trend_breakout.py` | Genetic Algorithm optimization of Donchian breakout (lookback, ATR, volume filter); Pareto frontier |
| 02 | `02_bayesian_mean_reversion.py` | Bayesian TPE optimization of RSI mean-reversion (period, oversold, overbought, lookback) |
| 03 | `03_deflated_sharpe_analysis.py` | DSR & PBO (CSCV) overfitting diagnostics on optimization trials |

Run them:

```bash
python examples/optimizer/01_ga_trend_breakout.py --generations 50 --population 50
python examples/optimizer/02_bayesian_mean_reversion.py --trials 100
python examples/optimizer/03_deflated_sharpe_analysis.py --trials 100 --folds 5
```

## `screening/` — point-in-time screeners and chains

Screeners read the 1D Parquet catalog and `fundamentals.duckdb` as of `--asof` and print a
symbol or JSON stream. Details in [`screening/README.md`](screening/README.md).

| # | File | Adds |
|---|---|---|
| 01 | `01_multibagger_screener.py` | Stage 2 momentum breakout candidates; ``--fundamentals`` adds the QGLP overlay (ROCE >= 20%, D/E <= 0.5) |
| 02 | `02_value_52w_low_screener.py` | Deep value near the 52-week low |
| 03 | `03_dividend_yield_screener.py` | High dividend yielders near the 52-week low |
| 04 | `04_intelligent_investor_screener.py` | Graham value and margin of safety |
| 05 | `05_net_net_screener.py` | Graham net-net (NCAV) deep asset value |
| 06 | `06_run_chain.py` | Run a screen, filter, size, execute chain from TOML; steps declare what they filter on and which extra columns they `report` |
| 08 | `08_swing_ap_setup_screener.py` | Swing-trading "A+ setup" scan: breakout/pullback, relative strength, volatility and liquidity gates |

```bash
python examples/screening/03_dividend_yield_screener.py --asof 2025-03-01 --top 5 --format symbols
python examples/screening/06_run_chain.py --chain examples/screening/chains/quality_value.toml --validate-only
python examples/screening/06_run_chain.py --chain examples/screening/chains/quality_value.toml --format csv
python examples/screening/06_run_chain.py --chain examples/screening/chains/fundamentals.toml --format symbols
python examples/screening/06_run_chain.py --describe
```

## `mutual_funds/` — NAV and SIP

No `Strategy` subclasses; direct NAV/SIP math over `honba_examples.amfi`.

| # | File | Purpose |
|---|---|---|
| 01 | `01_fetch_nav.py` | AMFI NAV history; resample to daily; handle missing days |
| 02 | `02_sip_backtest.py` | Fixed-date monthly SIP, units at NAV, XIRR |
| 03 | `03_category_analysis.py` | Category stats, rolling returns, quartile ranking |
| 04 | `04_portfolio_optimization.py` | Mean-variance allocation with constraints |

Run them:

```bash
python examples/mutual_funds/01_fetch_nav.py --scheme 120503 --start 2023-01-01 --end 2024-12-31
python examples/mutual_funds/02_sip_backtest.py --scheme 120503 --monthly 10000 --start 2023-01-01
python examples/mutual_funds/03_category_analysis.py --category "Equity:Large Cap" --start 2020-01-01
python examples/mutual_funds/04_portfolio_optimization.py --schemes 120503,120504,120505 --start 2022-01-01
```

## `options/` — chain, Greeks and strategy backtests

Hand-rolled legs (no `Strategy` subclass); see the catalog options strategies
(`options/nifty_short_straddle`, `options/banknifty_iron_condor`,
`options/expiry_day_scalp`) for the packaged versions.

| # | File | Strategy |
|---|---|---|
| 01 | `01_option_chain.py` | No strategy — chain snapshot, filter by expiry/strike/moneyness |
| 02 | `02_greeks_calculation.py` | No strategy — Black-Scholes Greeks per contract |
| 03 | `03_backtest_straddle.py` | Long ATM straddle, hold to expiry |
| 04 | `04_backtest_iron_condor.py` | Delta-hedged iron condor, expiry payoff |
| 05 | `05_expiry_day_strategy.py` | Intraday gamma scalping on expiry day |
| 06 | `06_nifty_920_straddle.py` | Short ATM straddle at 09:20 IST; 25 % per-leg stop, 15:20 square-off; sell-side STT |
| 07 | `07_large_qty_options_slicer.py` | Freeze-limit slicer: 5,000 contracts sliced ≤ freeze limit (1,800) from the instrument master |
| 08 | `08_iron_condor_hedging.py` | Iron condor with dynamic delta hedging and Greek risk bounds |

Run them:

```bash
python examples/options/01_option_chain.py
python examples/options/02_greeks_calculation.py --spot 24500 --strike 24500 --dte 7 --iv 0.15
python examples/options/03_backtest_straddle.py --strike 24500 --entry-dte 7
python examples/options/04_backtest_iron_condor.py --wings 200
python examples/options/05_expiry_day_strategy.py --dte 0 --step 10
python examples/options/06_nifty_920_straddle.py --spot 22000 --iv 0.15 --out straddle.json
python examples/options/07_large_qty_options_slicer.py --total-qty 5000 --out slices.json
python examples/options/08_iron_condor_hedging.py --spot 24000 --wings 200 --dte 7 --out hedge.json
```

## `stocksharp/` — ported StockSharp reference strategies (Phase 4)

Canonical Python implementations of classical `StockSharp.Algo.Strategies`
samples, ported to the Honba Python SDK. Offline and deterministic by default
(seeded RNGs, synthetic data).

| # | File | Strategy |
|---|---|---|
| 01 | `01_pairs_trading.py` | Statistical pairs trading: Engle-Granger cointegration (OLS hedge ratio + Dickey-Fuller spread test), z-score entry at ±2σ, exit at 0 |
| 02 | `02_twap_vwap.py` | Execution slicers: TWAP and VWAP intraday pacing with seeded microsecond jitter; freeze-limit verification |
| 03 | `03_turtle_breakout.py` | Turtle system: 20/55-day Donchian breakout, 2N ATR trailing stops, 4-unit pyramiding |
| 04 | `04_market_maker.py` | Passive bid/ask quoting: Avellaneda-Stoikov reservation price and spread with inventory skew and an OBI filter |
| 05 | `05_risk_parity.py` | Risk-parity rebalancer across 10 scrips: inverse 60-day volatility weights via `PortfolioStrategy` (StaticUniverse + InverseVolatility + EveryNDays) |

Run them:

```bash
python examples/stocksharp/01_pairs_trading.py --symbol-a ICICIBANK --symbol-b HDFCBANK --bars 252
python examples/stocksharp/02_twap_vwap.py --scheme twap --parent-qty 5000 --slices 75
python examples/stocksharp/02_twap_vwap.py --scheme vwap --parent-qty 5000 --slices 75 --seed 7
python examples/stocksharp/03_turtle_breakout.py --entry-window 20 --exit-window 10 --bars 200
python examples/stocksharp/04_market_maker.py --steps 240 --seed 42 --gamma 0.1 --k 1.5
python examples/stocksharp/05_risk_parity.py --symbols 10 --bars 260 --rebalance-days 20 --seed 42
pytest tests/unit -q -k "pairs_trading or twap_vwap or turtle_breakout or market_maker or risk_parity"
```

## `ai_research/` — AI-native autonomous research loop

Typed decision loop using `ScriptedFakeLlm` (no API key required).

| # | File | Shows |
|---|---|---|
| 01 | `01_natural_language_to_filter.py` | Prompt → JSON filter schema → duckdb execution |
| 02 | `02_mcp_query_schema.py` | Expose research catalog via MCP tools |
| 03 | `03_typed_llm_trade_decision.py` | Bar snapshot → typed Pydantic intent → risk gate |
| 04 | `04_autonomous_research_agent.py` | Hypothesis → backtest → evaluate → refine |
| 05 | `05_research_memory_bank.py` | Vector store of past experiments & findings |
| 06 | `06_factor_idea_generator.py` | Generate candidate alpha expressions |
| 07 | `07_regime_classification_agent.py` | Macro & volatility state machine |
| 09 | `09_backtest_refinement_agent.py` | Parameter refinement after drawdown analysis |

Run them:

```bash
python examples/ai_research/01_natural_language_to_filter.py --prompt "low pe high roe"
python examples/ai_research/02_mcp_query_schema.py
python examples/ai_research/03_typed_llm_trade_decision.py
python examples/ai_research/04_autonomous_research_agent.py --max-iterations 3
```

## `production/` — paper & live deployment

| # | File | Shows |
|---|---|---|
| 01 | `01_paper_trading.py` | Paper runner with Dhan adapter |
| 02 | `02_live_trading_dhan.py` | Live order submission with SEBI risk checks |
| 03 | `03_multi_account_execution.py` | Mirroring intents across master/child accounts |
| 04 | `04_monitoring.py` | Heartbeat, Prometheus metrics, alerting |
| 05 | `05_deployment.py` | Docker container & systemd service configuration |

Run them:

```bash
python examples/production/01_paper_trading.py --broker dhan
python examples/production/04_monitoring.py --port 9090
```
