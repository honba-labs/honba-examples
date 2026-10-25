# Walk-Forward Analysis (WFO) & Walk-Forward Efficiency (WFE)

## Overview

Walk-Forward Analysis is the gold standard for validating trading strategies. It
simulates how a strategy would have been developed in real time: calibrate on a
training window, test on the subsequent out-of-sample window, roll forward, repeat.

**Walk-Forward Efficiency (WFE)** quantifies how much of the in-sample edge
survives out-of-sample:

```
WFE = Mean(OOS Sharpe) / Mean(IS Sharpe)
```

A WFE ≥ 0.60 is the Honba certification threshold for "the edge is real."

---

## Why WFO Matters

### The Curve-Fitting Trap

Standard backtesting optimizes parameters on the *full* dataset. The best
parameters inevitably fit noise. WFO forces a temporal separation:
- **In-Sample (IS)**: Used *only* for parameter selection
- **Out-of-Sample (OOS)**: Held back, *never* seen during calibration

### The Rolling/Expanding Choice

| Window Type | Training Data | Pros | Cons |
|-------------|---------------|------|------|
| **Expanding** | Anchored at start, grows each fold | Uses all history; stable params | Regime changes contaminate |
| **Rolling** | Fixed-length window slides forward | Adapts to regime changes | Less training data per fold |

**Honba default: Expanding** (as documented in the literature).

---

## Honba WFO Implementation

### Function: `honba.algo_analytics.walk_forward.walk_forward`

```python
from honba.algo_analytics.walk_forward import walk_forward, WalkForwardGates

result = walk_forward(
    strategy=my_strategy,
    symbol="RELIANCE",
    exchange="NSE",
    start="2020-01-01",
    end="2024-12-31",
    train_months=24,
    test_months=6,
    n_folds=8,
    window="expanding",  # or "rolling"
    cash=1_000_000.0,
    # ... any Honba.backtest kwargs (timeframe, costs, fill, etc.)
)
```

### Each Fold Runs Two Full Backtests

```
Fold 0:  Train [2020-01, 2022-01)  →  Test [2022-01, 2022-07)
Fold 1:  Train [2020-01, 2022-07)  →  Test [2022-07, 2023-01)
Fold 2:  Train [2020-01, 2023-01)  →  Test [2023-01, 2023-07)
...
```

- **Fresh strategy instance per fold** (deep-copied for instances; classes/paths
  re-resolved). State from fold *i* training *never* leaks into fold *i* test.
- **Full Honba.backtest**: Next-open fills, NSE delivery costs, T+1 settlement.
- **Same backtest kwargs** for train and test (timeframe, costs, fill model).

### Output: `WalkForwardResult`

```python
@dataclass
class WalkForwardResult:
    folds: tuple[Fold, ...]
    train_months: int
    test_months: int
    window: Window
    gates: WalkForwardGates

    @property
    def oos_sharpes(self) -> list[float]: ...
    @property
    def mean_oos_sharpe(self) -> float: ...
    @property
    def oos_sharpe_std(self) -> float: ...
    @property
    def min_oos_sharpe(self) -> float: ...
    @property
    def max_oos_sharpe(self) -> float: ...
    @property
    def profitable_folds(self) -> int: ...
    @property
    def profitable_fraction(self) -> float: ...

    @property
    def checks(self) -> dict[str, bool]: ...
    @property
    def passed(self) -> bool: ...
    def summary(self) -> str: ...
```

### Fold Structure

```python
@dataclass
class Fold:
    index: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    in_sample: FoldStats      # IS Sharpe, return, DD, etc.
    out_of_sample: FoldStats  # OOS Sharpe, return, DD, etc.

    @property
    def oos_is_ratio(self) -> float:  # OOS Sharpe / IS Sharpe
```

---

## The Three Gates

Honba applies three independent gates to the fold OOS Sharpes:

| Gate | Default | Formula | Rationale |
|------|---------|---------|-----------|
| **Mean OOS Sharpe** | > 0.8 | `mean(OOS Sharpe) > 0.8` | Edge must be economically meaningful |
| **OOS Sharpe Std** | < 0.5 | `pstdev(OOS Sharpe) < 0.5` | Performance must be stable across regimes |
| **Profitable Folds** | ≥ 75% | `fraction(OOS Sharpe > 0) ≥ 0.75` | Majority of regimes must be profitable |

All three must pass (`result.passed == True`).

### Walk-Forward Efficiency (WFE)

Computed separately from the gates:

```python
mean_is = statistics.mean(f.in_sample.sharpe for f in result.folds)
mean_oos = statistics.mean(f.out_of_sample.sharpe for f in result.folds)
wfe = mean_oos / mean_is if mean_is > 0 else float("nan")
```

**Certification threshold: WFE ≥ 0.60**

This means the strategy retains at least 60% of its in-sample risk-adjusted
performance out-of-sample. Per Marcos López de Prado, WFE < 0.5 suggests the
in-sample optimization captured mostly noise.

---

## Example: `examples/backtesting/04_walk_forward.py`

```bash
# Synthetic bars (fast, deterministic)
python examples/backtesting/04_walk_forward.py --synthetic

# Real data from honba-data lakehouse
python examples/backtesting/04_walk_forward.py \
    --symbol RELIANCE \
    --start 2020-01-01 \
    --end 2024-12-31 \
    --train-months 24 \
    --test-months 6 \
    --n-folds 8
```

### Output Structure

```json
{
  "config": { ... },
  "gates": {
    "min_mean_oos_sharpe": 0.8,
    "max_oos_sharpe_std": 0.5,
    "min_profitable_fraction": 0.75,
    "checks": {
      "mean_oos_sharpe": true,
      "oos_sharpe_std": true,
      "profitable_folds": true
    },
    "passed": true
  },
  "wfe": {
    "mean_in_sample_sharpe": 1.85,
    "mean_out_of_sample_sharpe": 1.25,
    "walk_forward_efficiency": 0.676,
    "wfe_passed": true
  },
  "folds": [
    {
      "fold": 0,
      "train_start": "2020-01-01",
      "train_end": "2022-01-01",
      "test_start": "2022-01-01",
      "test_end": "2022-07-01",
      "in_sample": { "sharpe": 2.1, "total_return_pct": 45.2, ... },
      "out_of_sample": { "sharpe": 1.4, "total_return_pct": 22.1, ... },
      "oos_is_ratio": 0.667
    },
    ...
  ],
  "summary": "Walk-forward: 8 folds ...\nChecks: ...\nRESULT: PASSED"
}
```

---

## Practical Guidelines

### Choosing Fold Geometry

| Parameter | Guideline |
|-----------|-----------|
| `train_months` | ≥ 12 (1+ years for stable parameter estimation) |
| `test_months` | 3–6 (long enough for multiple trades, short enough for many folds) |
| `n_folds` | ≥ 5 (need spread for std calculation; 8 is standard) |
| Total range | `train_months + n_folds × test_months` ≤ available data |

**Example**: 24m train + 8 × 6m test = 72 months (6 years) of data needed.

### Interpreting Results

| Scenario | Diagnosis |
|----------|-----------|
| High IS, Low OOS, Low WFE | Classic overfit; parameters fit noise |
| High IS, High OOS, High WFE | **Genuine edge** — proceed to live validation |
| Low IS, Low OOS | No edge; strategy doesn't work |
| High OOS Std, Some Fails | Regime-dependent; works in trends not ranges (or vice versa) |
| WFE ≥ 0.60 but gates fail | Unstable performance; needs regime filter |

### Common Pitfalls

1. **Data leakage**: Using future data in indicators (e.g., `shift(-1)`). Honba's
   `Strategy.on_bar` receives bars in strict temporal order — no lookahead possible.

2. **State leakage**: Strategy instance carries state from IS to OOS. Honba's
   `walk_forward` deep-copies instances per fold — this cannot happen.

3. **Insufficient trades per fold**: If a fold has < 5 trades, its Sharpe is
   meaningless. Check `fold.in_sample.n_trades` and `fold.out_of_sample.n_trades`.

4. **Regime change in test window**: A 6-month test window spanning a bull→bear
   transition will show high variance. Consider longer test windows or regime labels.

5. **Multiple testing on WFO itself**: If you run WFO on 20 strategies and pick
   the one that passes, you've just moved the multiple testing problem up a level.
   Apply DSR/PBO to the WFO results!

---

## Integration with DSR/PBO

WFO produces the fold-level IS/OOS fitness values needed for PBO:

```python
from honba.algo_analytics.walk_forward import walk_forward
from honba_examples.optimizer import compute_pbo_cscv, compute_dsr

wf = walk_forward(strategy, ...)

# PBO from WFO folds
pbo = compute_pbo_cscv(
    is_sharpes=[f.in_sample.sharpe for f in wf.folds],
    oos_sharpes=[f.out_of_sample.sharpe for f in wf.folds],
    n_folds=len(wf.folds),
)

# DSR from best fold's returns
best_fold = max(wf.folds, key=lambda f: f.in_sample.sharpe)
dsr = compute_dsr(
    observed_sharpe=best_fold.in_sample.sharpe,
    n_trials=number_of_parameter_combos_tested,
    returns=best_fold_returns,  # Need to extract from backtest
)
```

---

## Running in CI

The WFO example is marked `slow` (full backtests per fold). In CI:

```bash
# Skip slow tests (including WFO)
pytest -q -m "not slow"

# Run only WFO
pytest -q tests/unit/test_04_walk_forward.py
```

---

## References

1. Pardo, R. (2008). *The Evaluation and Optimization of Trading Strategies*. Wiley.
2. López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley.
   Chapter 12: Walk-Forward Analysis.
3. Honba `honba.algo_analytics.walk_forward` module.
4. Honba `honba-ya-enhancer/src/walk_forward.rs` (Rust implementation).