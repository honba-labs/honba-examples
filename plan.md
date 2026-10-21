# Master Plan: 本場・例 honba-examples

> **Canonical reference strategies and educational tutorials.**  
> Demonstrates the Python-First Strategy SDK (`honba`), event-driven backtesting (`honba-ya-backtest`), the Automatic Strategy Enhancer (GA & Bayesian optimization, Walk-Forward Analysis, DSR/PBO), Indian options microstructure (NSE/NFO), and the AI-native autonomous research loop (`honba-nou`).

---

## 0. Executive Summary & Strategy Taxonomy

`honba-examples` serves as the runnable reference library for the entire Honba platform. Every example in this repository adheres to five non-negotiable principles:
1. **Python-First Ergonomics**: Written cleanly in Python 3.12+ using the native `honba` SDK (`Strategy` base class, declarative `@parameter` definitions, and type annotations).
2. **Research-Live Parity**: The exact same strategy class runs identically in historical backtesting, paper trading, and multi-broker live execution.
3. **Realistic Indian Microstructure**: Accurate statutory Indian taxes (STT, Stamp Duty, GST, Exchange turnover fees), T+1 Demat settlement, MIS 15:20 IST auto-square-off, and SEBI single-order freeze limits.
4. **Anti-Overfitting Validation**: Every backtested strategy includes Deflated Sharpe Ratio (DSR), Probability of Backtest Overfitting (PBO), and Monte Carlo stress tests.
5. **Zero Lookahead Bias**: Point-in-time data loading from the Parquet Lakehouse (`honba-data`) with strict temporal ordering.

---

## 1. Directory Structure Target

```text
honba-examples/
├── examples/
│   ├── 01_python_first_basics/      # Core SDK lifecycle & order types
│   ├── 02_strategy_optimizer/       # Genetic Algorithm & Bayesian tuning (like Jesse)
│   ├── 03_indian_derivatives/       # NIFTY/BankNIFTY options selling & straddles
│   ├── 04_factor_and_screening/     # Alpha30, momentum, and multibagger screeners
│   └── 05_ai_native_research/       # Claude/Gemini hypothesis loop & prompt replayer
├── src/
│   └── honba_examples/              # Shared utilities, CLI runners, and plotters
├── docs/                            # In-depth strategy whitepapers & mathematical proofs
├── tests/                           # Conformance, regression, and snapshot tests
├── pyproject.toml                   # Single source of truth for dependencies
├── Makefile                         # Unified runner (install, test, backtest)
└── README.md                        # Concise quickstart and examples index
```

---

## 2. Phase-by-Phase Implementation Roadmap

### Phase 1: Python-First Strategy SDK Fundamentals (M0) — Week 1–2
- [ ] **1.1 Canonical Lifecycle Demonstration**
  - [ ] Implement `01_buy_and_hold.py` demonstrating `on_start`, `on_bar`, and `on_stop`.
  - [ ] Implement `02_sma_crossover.py` demonstrating streaming indicator updates (`EMA`, `SMA`).
  - [ ] Implement `03_bracket_order_mis.py` demonstrating Take-Profit / Stop-Loss bracket orders and MIS intraday margin.
- [ ] **1.2 Position Management & Order Events**
  - [ ] Implement `04_partial_fill_handling.py` demonstrating `on_order_event` callbacks and order modification.
  - [ ] Implement `05_multi_symbol_portfolio.py` managing positions across 5 NIFTY scrips simultaneously.

---

### Phase 2: Strategy Tester & Automatic Strategy Enhancer (M1) — Week 3–4
- [ ] **2.1 Genetic Algorithm (GA) Optimization (like Jesse)**
  - [ ] Implement `01_ga_trend_breakout.py` with declarative `Parameter(min=..., max=..., step=...)`.
  - [ ] Configure tournament selection, crossover, and mutation across a 50-generation run.
  - [ ] Plot the resulting Pareto-optimal frontier (Sharpe vs Max Drawdown).
- [ ] **2.2 Bayesian Hyperparameter Optimization**
  - [ ] Implement `02_bayesian_mean_reversion.py` using Tree-structured Parzen Estimators (TPE) over high-dimensional search spaces.
- [ ] **2.3 Walk-Forward Analysis (WFO)**
  - [ ] Implement `03_walk_forward_momentum.py` partitioning 5 years of historical data into alternating in-sample and out-of-sample slices.
  - [ ] Assert Walk-Forward Efficiency $\text{WFE} \ge 0.60$.
- [ ] **2.4 Overfitting Diagnostics & Monte Carlo**
  - [ ] Implement `04_deflated_sharpe_analysis.py` computing Marcos López de Prado's DSR and PBO.
  - [ ] Implement `05_monte_carlo_drawdown.py` permuting 1,000 trade sequences and injecting 2x slippage shocks.

---

### Phase 3: Indian Derivatives & Options Microstructure (M2) — Week 5–6
- [ ] **3.1 Intraday Options Selling**
  - [ ] Implement `01_nifty_920_straddle.py` (Rule: enter at 09:20 IST, 25% stop-loss per leg, 15:20 square-off).
  - [ ] Model option premium STT (0.0625% sell-side) and ICCL settlement rules.
- [ ] **3.2 Freeze-Limit Auto-Slicing**
  - [ ] Implement `02_large_qty_options_slicer.py` submitting 5,000 NIFTY contracts, verifying that `honba-te-slicer` splits the order into slices $\le 1,800$.
- [ ] **3.3 Delta-Neutral Volatility Arbitrage**
  - [ ] Implement `03_iron_condor_hedging.py` with dynamic delta hedging and Greek risk bounds.

---

### Phase 4: Ported StockSharp Reference Strategies (M3) — Week 7–8
> Canonical Python reference implementations of classical strategies ported from StockSharp.Algo.Strategies:

- [ ] **4.1 Statistical Pairs Trading (`04_stocksharp_pairs_trading.py`)**
  - [ ] Cointegration test (Engle-Granger) on two correlated NIFTY scrips (e.g. ICICIBANK vs HDFCBANK).
  - [ ] Real-time Z-score spread tracking with two-sided entry at $\pm 2.0\sigma$ and mean exit at $0.0$.
- [ ] **4.2 Algorithmic Execution Slicers (`05_stocksharp_twap_vwap.py`)**
  - [ ] Port of StockSharp TWAP and VWAP execution algorithms with intraday volume pacing and randomized microsecond jitter.
- [ ] **4.3 Turtle Trading System (`06_stocksharp_turtle_breakout.py`)**
  - [ ] Classical 20/55-day Donchian channel breakout with $2N$ ATR trailing stops and 4-unit pyramiding.
- [ ] **4.4 Spread Quoting & Market Making (`07_stocksharp_market_maker.py`)**
  - [ ] Passive bid/ask quoting with Avellaneda-Stoikov inventory skew and Order Book Imbalance filter.
- [ ] **4.5 Risk Parity Portfolio Rebalancer (`08_stocksharp_risk_parity.py`)**
  - [ ] Dynamic asset allocation weighting 10 scrips inversely to their 60-day historical volatility.

---

### Phase 5: Factor Investing & Multibagger Screener (M4) — Week 9–10
- [ ] **4.1 Alpha30 Factor Model**
  - [ ] Implement `01_alpha30_cross_sectional.py` computing daily factor scores across NIFTY 500 stocks.
  - [ ] Long the top decile and rebalance monthly using zero-copy Arrow queries against the Parquet Lakehouse.
- [ ] **4.2 Multibagger Fundamentals Screener**
  - [ ] Implement `02_multibagger_screener.py` filtering small-cap equities for ROCE > 20%, low debt-to-equity, and momentum breakout.

---

### Phase 6: AI-Native Quant Research & Odysseus Loop (M5) — Week 11–12
- [ ] **5.1 MCP Quantitative Agent Workflow**
  - [ ] Implement `01_claude_autonomous_quant.py` invoking the `honba-nou-mcp` server tools.
  - [ ] AI inspects indicator catalog, formulates a mean-reversion hypothesis, runs backtest, and evaluates DSR.
- [ ] **5.2 Cryptographic Prompt Replayer**
  - [ ] Implement `02_deterministic_ai_replay.py` replaying 100 historical AI decisions from SHA-256 prompt cache without calling external APIs.
- [ ] **5.3 AI Strategy Auto-Enhancer**
  - [ ] Implement `03_ai_drawdown_refinement.py` where AI diagnoses a 12% drawdown streak, adds an ADX trend filter, and re-evaluates out-of-sample.

---

## 3. Verification & CI Test Suite

- [ ] `pytest tests/`: All example scripts are covered by smoke tests ensuring zero syntax errors or runtime exceptions.
- [ ] `make test-parity`: Asserts that Python backtest returns match compiled Rust backtest returns within $1 \times 10^{-6}$.
- [ ] `ruff check . && ruff format --check .`: Strict PEP 8 linting and formatting across all examples.
- [ ] `pyright`: Full static type checking on `src/honba_examples/`.
