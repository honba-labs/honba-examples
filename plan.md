# Master Plan: 本場・例 honba-examples

> **Canonical reference strategies and educational tutorials.**
> Demonstrates the Python-First Strategy SDK (`honba`, from `honba-tamashii`), event-driven backtesting (`honba-ya`: `honba-ya-backtest`), the Automatic Strategy Enhancer (`honba-ya-enhancer`: GA & Bayesian optimization, Walk-Forward Analysis, DSR/PBO), Indian options microstructure (NSE/NFO), and the AI-native autonomous research loop (`honba-nou`).

---

## 0. Executive Summary & Strategy Taxonomy

`honba-examples` is the runnable reference library for the Honba platform. Every example adheres to six non-negotiable principles:
1. **Python-First Ergonomics**: Clean Python written against the native `honba` SDK (`Strategy` base class, declarative `Parameter(min=, max=, step=)` definitions, type annotations).
2. **Research-Live Parity**: The same strategy class runs identically in historical backtesting, paper trading, and multi-broker live execution (`--adapter fake|dhan|...`).
3. **Realistic Indian Microstructure**: Statutory taxes (STT, stamp duty, GST, exchange turnover fees), T+1 settlement, MIS 15:20 IST auto-square-off, SEBI single-order freeze limits. Rates, lot sizes and freeze limits are **read from the cost model / instrument master, never hard-coded** (they change by NSE/SEBI circular).
4. **Anti-Overfitting Validation**: Backtested strategies report Deflated Sharpe Ratio (DSR), Probability of Backtest Overfitting (PBO), and Monte Carlo stress results.
5. **Zero Lookahead Bias**: Point-in-time loading from the Parquet Lakehouse (`honba-data`) with strict temporal ordering and explicit as-of dates.
6. **Offline & Deterministic by default**: `fake` adapter + synthetic bars, fixed seeds, `ScriptedFakeLlm` for AI examples. Money/price/qty arithmetic uses `Decimal` (AGENTS.md "Zero Floats Policy"); floats are allowed only for statistics (Sharpe, z-scores, Greeks).

**Repo conventions every new example must follow** (already enforced for existing ones):
- Numbered scripts `NN_snake_case.py` inside a topic directory; **numbers are never reused or shifted** (e.g. `ai_research/08` stays reserved).
- Module docstring with a `Pitfalls::` section (enforced by `tests/unit/test_pitfalls_sections.py`).
- Topic `README.md` table row + run command, and a row in [`examples/README.md`](examples/README.md).
- TDD: a `tests/unit/test_NN_<name>.py` is written first (loads the script via `importlib.util.spec_from_file_location`, see [`tests/README.md`](tests/README.md)); shared logic goes to `src/honba_examples/`.

---

## 1. Directory Structure (current layout + planned additions)

`[new]` = directory/file to be created by this plan. Everything else already exists and is **extended, not duplicated**.

```text
honba-examples/
├── examples/
│   ├── README.md                 # examples index (add a row for every new script)
│   ├── basic/                    # 01–05  broker adapters: connect, instruments, quotes, paper orders, positions
│   ├── candles/                  # 01–04  bar construction & aggregation
│   ├── storage/                  # 01–04  Parquet catalog, bhavcopy/AMFI import, tearsheet export
│   ├── strategies/               # 01–11  SDK lifecycle, indicators, sizing, risk, declarative, pipelines
│   │                             #        + 12–16  Phase 1 (lifecycle, bracket/MIS, order events, 5-scrip book, dynamic sizing)
│   ├── backtesting/              # 01–11  costs, latency, walk-forward, Monte Carlo, sweeps, SIP, rebalancer
│   │                             #        (04, 05, 06 are upgraded in Phase 2)
│   ├── optimizer/                # 01–03  GA, Bayesian (TPE), DSR/PBO overfitting diagnostics
│   ├── options/                  # 01–05  chain, Greeks, straddle, iron condor, expiry day
│   │                             #        + [new] 06–08  Phase 3 (9:20 short straddle, freeze-limit slicer, delta hedging)
│   ├── stocksharp/      [new]    # 01–05  Phase 4 ported StockSharp reference strategies
│   ├── universes/                # 01–08  NIFTY50/BANKNIFTY/Alpha30 universes & rebalancers
│   │                             #        + [new] 09  Alpha30 cross-sectional factor scores
│   ├── screening/                # 01–08  multibagger, value, dividend, Graham, net-net, chains, swing
│   │                             #        (01 is upgraded in Phase 5)
│   ├── mutual_funds/             # 01–04  NAV, SIP, category analysis, optimization
│   ├── ai_research/              # 01–07, 09 (08 reserved)  NL→filter, MCP schema, typed LLM decision
│   │                             #        + [new] 10–12  Phase 6 (MCP quant agent, SHA-256 replay, drawdown refinement)
│   └── production/               # 01–05  paper, live (Dhan), multi-account, monitoring, deployment
├── src/
│   └── honba_examples/           # shared helpers: base, backtest, metrics, catalog, settlement, artifact,
│       │                         # output, cli, bars, sip*, pipeline/, ewr/, screening/, ...
│       ├── optimizer/            # search-space/Parameter adapters, Pareto + equity plotters, DSR/PBO math
│       ├── options/     [new]    # STT/ICCL cost helpers, freeze-limit + slice verifier, Greek-bound checks
│       └── ai_replay/   [new]    # SHA-256 prompt cache + replay journal helpers
├── docs/                         # multibagger_screening_research.md, refactor-plan.md, roadmap-chaining.md
│                                 #   + [new] whitepapers: dsr_pbo.md, wfo_efficiency.md, options_microstructure.md
├── tests/
│   ├── unit/                     # fast offline per-example tests (test_NN_<script>.py)
│   ├── integration/              # real-catalog / pipeline tests (skip when data is absent)
│   └── snapshots/                # golden outputs
├── pyproject.toml                # single source of truth for dependencies
├── pytest.ini / pyrightconfig.json
├── Makefile                      # install, test, lint, test-all  (+ [new] test-parity, typecheck)
└── README.md                     # concise quickstart and examples index
```

Related repos this plan depends on (sibling checkouts in the workspace): `honba-tamashii` (Python SDK / PyO3), `honba-ya` (backtest, enhancer), `honba-te` (`honba-te-slicer`), `honba-nou` (`honba-nou-mcp`, `honba-nou-genetic-engine`), `honba-data` (lakehouse), `honba-strategies` (catalog strategies).

---

## 2. Phase-by-Phase Implementation Roadmap

### Phase 1: Python-First Strategy SDK Fundamentals (M0) — Week 1–2 ✅
Home: `examples/strategies/` (adapter-only material stays in `examples/basic/`).

- [x] **1.1 Canonical Lifecycle Demonstration**
  - [x] `strategies/12_buy_and_hold_lifecycle.py` — `on_start`, `on_bar`, `on_stop`.
  - [x] SMA/EMA crossover with streaming indicators — **already covered** by `strategies/01_first_strategy.py` and `strategies/02_with_indicators.py` (and `backtesting/01_first_backtest.py`). Added `EMA` variant to `02_with_indicators.py`; no new file.
  - [x] `strategies/13_bracket_order_mis.py` — take-profit / stop-loss bracket orders and MIS intraday margin with 15:20 IST auto-square-off (builds on `04_risk_management.py`).
- [x] **1.2 Position Management & Order Events**
  - [x] `strategies/14_partial_fill_handling.py` — `on_order_event` callbacks and order modification.
  - [x] `strategies/15_multi_symbol_portfolio.py` — positions across 5 NIFTY scrips simultaneously (generalises `05_multi_instrument.py`'s `BasketSma`; per-symbol sizing and risk caps).
  - [x] `strategies/16_dynamic_position_sizing.py` — First-class dynamic position sizing demonstration comparing fixed lot, fixed fractional (1% risk of equity per trade), and ATR volatility-targeted sizing against NIFTY historical data.
- [x] Tests: `tests/unit/test_12_…` through `test_16_…`; update `strategies/README.md` + `examples/README.md`.

---

### Phase 2: Strategy Tester & Automatic Strategy Enhancer (M1) — Week 3–4 ✅
Home: new `examples/optimizer/` for search/diagnostics; existing `examples/backtesting/` scripts are **upgraded** (they already exist as plain versions).

- [x] **2.1 Genetic Algorithm (GA) Optimization (like Jesse)**
  - [x] `optimizer/01_ga_trend_breakout.py` with declarative `Parameter(min=..., max=..., step=...)`, via `honba-ya-enhancer` / `honba-nou-genetic-engine`.
  - [x] Tournament selection, crossover, mutation across a 50-generation run (seeded, reproducible).
  - [x] Plot the Pareto-optimal frontier (Sharpe vs Max Drawdown) — reuse the Pareto logic in `backtesting/06_concurrent_backtest.py`, moved into `honba_examples/optimizer/`.
- [x] **2.2 Bayesian Hyperparameter Optimization**
  - [x] `optimizer/02_bayesian_mean_reversion.py` — Tree-structured Parzen Estimators (TPE) over a high-dimensional search space.
- [x] **2.3 Walk-Forward Analysis (WFO)**
  - [x] Upgrade `backtesting/04_walk_forward.py` (`SmaWalk`) to a momentum strategy over 5 years of data, with alternating in-sample / out-of-sample slices.
  - [x] Assert Walk-Forward Efficiency $\text{WFE} \ge 0.60$ (as a reported gate on real data; synthetic-data runs report it without asserting).
- [x] **2.4 Overfitting Diagnostics & Monte Carlo**
  - [x] `optimizer/03_deflated_sharpe_analysis.py` — Marcos López de Prado's DSR and PBO (CSCV) over the trials produced by 2.1/2.2.
  - [x] Upgrade `backtesting/05_monte_carlo.py` (`SmaMonteCarlo`): permute 1,000 trade sequences and inject 2x slippage shocks; report drawdown distribution.
- [x] Docs: `docs/dsr_pbo.md`, `docs/wfo_efficiency.md`.

---

### Phase 3: Indian Derivatives & Options Microstructure (M2) — Week 5–6
Home: `examples/options/` (existing 01–05 are chain, Greeks, long straddle, iron condor, expiry day; new scripts continue at 06).

- [ ] **3.1 Intraday Options Selling**
  - [ ] `options/06_nifty_920_straddle.py` — enter 09:20 IST, 25% stop-loss per leg, 15:20 square-off (short counterpart to the long straddle in `03_backtest_straddle.py`; compare with catalog `options/nifty_short_straddle`).
  - [ ] Model option-premium STT (sell side) and ICCL settlement rules in `honba_examples/options/`. **Use the current statutory rate from the cost model** (the originally planned 0.0625% is outdated; verify against the latest SEBI/NSE circular).
- [ ] **3.2 Freeze-Limit Auto-Slicing**
  - [ ] `options/07_large_qty_options_slicer.py` — submit 5,000 NIFTY contracts and verify `honba-te-slicer` splits the order into slices ≤ the instrument's freeze limit (currently planned as 1,800; read it from the instrument master).
- [ ] **3.3 Delta-Neutral Volatility Arbitrage**
  - [ ] `options/08_iron_condor_hedging.py` — extends `04_backtest_iron_condor.py` with dynamic delta hedging and Greek risk bounds (uses `02_greeks_calculation.py` math).
- [ ] Docs: `docs/options_microstructure.md`.

---

### Phase 4: Ported StockSharp Reference Strategies (M3) — Week 7–8
Home: new `examples/stocksharp/` (numbered 01–05; the old `04_/05_/06_stocksharp_*` prefixes are dropped because they would collide with each directory's own numbering).

> Canonical Python reference implementations of classical strategies ported from StockSharp.Algo.Strategies.

- [ ] **4.1 Statistical Pairs Trading** — `stocksharp/01_pairs_trading.py`
  - [ ] Engle-Granger cointegration test on two correlated NIFTY scrips (e.g. ICICIBANK vs HDFCBANK).
  - [ ] Real-time z-score spread with two-sided entry at $\pm 2.0\sigma$ and mean exit at $0.0$.
- [ ] **4.2 Algorithmic Execution Slicers** — `stocksharp/02_twap_vwap.py`
  - [ ] TWAP and VWAP execution algorithms with intraday volume pacing and randomized microsecond jitter (seeded RNG for determinism).
- [ ] **4.3 Turtle Trading System** — `stocksharp/03_turtle_breakout.py`
  - [ ] Classical 20/55-day Donchian channel breakout with $2N$ ATR trailing stops and 4-unit pyramiding.
- [ ] **4.4 Spread Quoting & Market Making** — `stocksharp/04_market_maker.py`
  - [ ] Passive bid/ask quoting with Avellaneda-Stoikov inventory skew and Order Book Imbalance filter.
- [ ] **4.5 Risk Parity Portfolio Rebalancer** — `stocksharp/05_risk_parity.py`
  - [ ] Dynamic allocation across 10 scrips weighted inversely to 60-day historical volatility (compose with `PortfolioStrategy` as in `strategies/06_equal_weight_rebalance.py`).
- [ ] Create `stocksharp/README.md`; tests `tests/unit/test_01_pairs_trading.py` etc.

---

### Phase 5: Factor Investing & Multibagger Screener (M4) — Week 9–10
Homes: `examples/universes/` (Alpha30 lives here: 03–08) and `examples/screening/`.

- [ ] **5.1 Alpha30 Factor Model**
  - [ ] `universes/09_alpha30_cross_sectional.py` — daily factor scores across NIFTY 500 stocks; long the top decile and rebalance monthly using zero-copy Arrow queries against the Parquet Lakehouse. Reuse `07_alpha30_backtest.py` / `08_alpha30_union_ewr_backtest.py` plumbing (`honba_examples/ewr/`).
- [ ] **5.2 Multibagger Fundamentals Screener**
  - [x] `screening/01_multibagger_screener.py` — Stage 2 momentum breakout candidates **already exists**.
  - [ ] Extend it (or add a `--fundamentals` chain in `screening/chains/`) to filter small-cap equities for ROCE > 20%, low debt-to-equity, plus the momentum breakout; see `docs/multibagger_screening_research.md` and `screening/06_run_chain.py`.

---

### Phase 6: AI-Native Quant Research & Odysseus Loop (M5) — Week 11–12
Home: `examples/ai_research/` (existing: 01–07, 09; **slot 08 stays reserved**; `02_autoresearch_loop.py`, `04`, `05`, `07` are currently design-only placeholders).

- [ ] **6.1 MCP Quantitative Agent Workflow**
  - [ ] `ai_research/10_claude_autonomous_quant.py` — invokes the `honba-nou-mcp` server tools (builds on the static tool enumeration in `06_mcp_integration.py`). The AI inspects the indicator catalog, formulates a mean-reversion hypothesis, runs a backtest, and evaluates DSR. Offline default via `ScriptedFakeLlm`; real Claude/Gemini behind an explicit flag.
  - [ ] Promote the design-only `02_autoresearch_loop.py` to runnable using the same loop.
- [ ] **6.2 Cryptographic Prompt Replayer**
  - [ ] `ai_research/11_deterministic_ai_replay.py` — replays 100 historical AI decisions from a SHA-256 prompt cache with no external API calls (builds on the Journal v1 audit records of `09_typed_llm_decision.py`; helpers in `honba_examples/ai_replay/`).
- [ ] **6.3 AI Strategy Auto-Enhancer**
  - [ ] `ai_research/12_ai_drawdown_refinement.py` — the AI diagnoses a 12% drawdown streak, adds an ADX trend filter, and re-evaluates out-of-sample (reuses the walk-forward harness from Phase 2). Typed `LlmDecision` guard rules from `honba_examples/decision_guard.py` apply.
- [ ] Update `ai_research/README.md`; keep a `Pitfalls::` section on training-data look-ahead.

---

### Phase 7: Polyrepo Decoupling, Multi-Broker Parity & Property Testing (M6)
> **Eliminating cross-repo relative path fragility, verifying multi-broker live/paper parity, and adding property-based testing.**

- [ ] **7.1 Polyrepo Dependency Decoupling**
  - [ ] Replace fragile relative filesystem paths (`../honba-strategies`) with versioned package dependencies or workspace virtualenv configuration.
  - [ ] Add contract tests verifying examples run against published `honba` wheels without needing Rust compiler toolchain locally.
- [ ] **7.2 Multi-Broker Parity Demonstration**
  - [ ] `production/06_multi_broker_dispatch.py` — Demonstrating seamless strategy deployment across Zerodha Kite, Upstox v2, and Angel One SmartAPI using uniform adapter interfaces.
- [ ] **7.3 Property-Based Testing with Hypothesis**
  - [ ] Add property-based testing (`tests/unit/test_metrics_property.py`) verifying Sharpe, Sortino, Drawdown, and position sizing invariants across synthetic random equity curves.

---

## 3. Verification & CI Test Suite

Existing (in [`Makefile`](Makefile)): `make install`, `make test`, `make lint`, `make test-all`.

- [ ] `pytest -q` (`make test`): every example script is covered by a unit test in `tests/unit/` (no network, synthetic data); real-data integration tests in `tests/integration/` skip when data is unavailable. `pytest -m "not slow"` excludes long GA/Bayesian/WFO runs.
- [ ] `make test-parity` **[new target]**: asserts Python backtest returns match the compiled Rust backtest (`honba-ya-backtest`) within $1 \times 10^{-6}$, using golden vectors in `tests/snapshots/`.
- [ ] `ruff check . && ruff format --check .` (`make lint`): strict linting and formatting across `examples/`, `src/` and `tests/`.
- [ ] `pyright` **[new target `make typecheck`]**: full static type checking on `src/honba_examples/` (`pyrightconfig.json` already points at `.venv`; add `include`).
- [ ] `pyproject.toml`: bump `requires-python` (currently `>=3.10`) and ruff `target-version` to the Python 3.12+ stated in §0; add optional extras `optimizer = ["optuna", "scipy", "matplotlib"]`, `ai = [...]`.
- [ ] `make test-all` = lint + typecheck + test + test-parity.
