# Deflated Sharpe Ratio (DSR) & Probability of Backtest Overfitting (PBO)

## Overview

This document describes the implementation and usage of two critical overfitting diagnostics
in the Honba platform:

1. **Deflated Sharpe Ratio (DSR)** — Adjusts the observed Sharpe ratio for multiple
   testing, non-normality of returns, and sample length (Bailey & López de Prado, 2014).
2. **Probability of Backtest Overfitting (PBO)** — Uses Combinatorially Symmetric
   Cross-Validation (CSCV) to estimate the probability that a strategy's performance
   is due to overfitting rather than genuine edge.

Both are implemented in:
- Rust: `honba-ya-enhancer/src/overfitting_guardrails.rs`
- Python: `honba.algo_analytics.deflated_sharpe` and walk-forward fold analysis

---

## Deflated Sharpe Ratio (DSR)

### Theory

The standard Sharpe ratio assumes a single strategy tested once on independent data.
In practice, researchers test many variants (parameter combinations, different assets,
different lookbacks) and report the best. This **selection bias** inflates the
observed Sharpe.

The DSR computes:
```
DSR = P(SR* > 0 | SR_obs, N_trials, skew, kurtosis, T)
```

Where:
- `SR_obs` = observed Sharpe ratio (annualized)
- `N_trials` = number of independent strategy variants tested
- `skew` = skewness of return distribution
- `kurtosis` = kurtosis of return distribution (3 for normal)
- `T` = number of return periods (e.g., 252 for daily data over 1 year)

### Formula (Bailey & López de Prado, Eq. 10–12)

1. **Variance of Sharpe estimator** (Mertens, 2002):
   ```
   V[SR] = (1 + SR²/2) / (T - 1)
   ```

2. **Expected maximum Sharpe under null (SR = 0)**:
   ```
   μ_max = E[max Z] × √V[SR]
   E[max Z] ≈ √(2 ln N) - 0.5 × (ln ln N + ln 4π) / √(2 ln N)  (for large N)
   ```

3. **Standard deviation of maximum**:
   ```
   σ_max = √[V[SR] × (1 - 2/π)]
   ```

4. **Non-normality adjustment**:
   ```
   μ_adj = 1 + 0.5 × skew + 0.125 × (kurtosis - 3)
   σ_adj = √[1 + skew² + 0.5 × (kurtosis - 3)]
   μ_max *= μ_adj
   σ_max *= σ_adj
   ```

5. **DSR**:
   ```
   DSR = Φ((SR_obs - μ_max) / σ_max)
   ```

### Interpretation

| DSR Range | Interpretation |
|-----------|----------------|
| DSR ≥ 3.0 | **Robust**: Strategy likely has genuine edge after multiple testing correction |
| 1.5 ≤ DSR < 3.0 | **Marginal**: Some edge may exist but sensitive to assumptions |
| DSR < 1.5 | **Likely overfit**: Observed Sharpe likely due to selection bias |

### Usage in Honba

```python
from honba.algo_analytics.deflated_sharpe import deflated_sharpe

# Basic usage
dsr = deflated_sharpe(
    observed_sr=2.5,  # Your observed annual Sharpe
    n_trials=100,  # Number of parameter combos tested
    skew=-0.5,  # Return skewness (estimate from backtest)
    kurtosis=4.2,  # Return kurtosis (estimate from backtest)
    t=252,  # Daily periods in backtest
)

# With automatic moment estimation from returns
from honba_examples.optimizer import compute_dsr

dsr_result = compute_dsr(
    observed_sharpe=2.5,
    n_trials=100,
    returns=daily_returns,  # List of daily returns
)
```

### Example: `examples/optimizer/03_deflated_sharpe_analysis.py`

This example demonstrates DSR computation on synthetic optimization trials:

```bash
python examples/optimizer/03_deflated_sharpe_analysis.py --trials 100 --true-sharpe 1.5
```

Output includes:
- Observed vs Deflated Sharpe
- Skew/Kurtosis estimates
- Overfitting verdict

---

## Probability of Backtest Overfitting (PBO)

### Theory

PBO estimates the probability that a strategy's in-sample performance does not
generalize out-of-sample. It uses **Combinatorially Symmetric Cross-Validation (CSCV)**:

1. Split backtest period into `k` folds (e.g., k=5)
2. For each fold, train on `k-1` folds, test on 1 fold (standard CV)
3. CSCV: generate all `2^k` symmetric train/test partitions
4. For each partition, compute fitness difference: `Δ = fitness_OOS - fitness_IS`
5. PBO = fraction of partitions where `Δ < 0` (strategy degrades OOS)

### CSCV Geometry

For k=5 folds, there are 16 symmetric partitions (2^(k-1)). Each partition
alternates IS/OOS blocks, ensuring no temporal leakage.

### Interpretation

| PBO Range | Interpretation |
|-----------|----------------|
| PBO < 0.3 | **Low**: Strategy generalizes well |
| 0.3 ≤ PBO < 0.5 | **Elevated**: Some OOS degradation observed |
| PBO ≥ 0.5 | **High**: Strategy likely overfit; degrades OOS in majority of partitions |

### Usage in Honba

```python
from honba_examples.optimizer import compute_pbo_cscv

# From walk-forward folds
pbo_result = compute_pbo_cscv(
    is_sharpes=[1.8, 2.1, 1.5, 2.0, 1.9],  # In-sample Sharpes per fold
    oos_sharpes=[1.2, 1.8, 0.8, 1.5, 1.1],  # Out-of-sample Sharpes per fold
    n_folds=5,
)
```

### Integration with Walk-Forward Analysis

The walk-forward analysis (`honba.algo_analytics.walk_forward`) produces
in-sample and out-of-sample Sharpe ratios per fold. These can be fed directly
into the PBO calculator:

```python
from honba.algo_analytics.walk_forward import walk_forward
from honba_examples.optimizer import compute_pbo_cscv

wf_result = walk_forward(strategy, symbol="RELIANCE", start="2020-01-01", ...)

is_sharpes = [f.in_sample.sharpe for f in wf_result.folds]
oos_sharpes = [f.out_of_sample.sharpe for f in wf_result.folds]

pbo = compute_pbo_cscv(is_sharpes, oos_sharpes, len(wf_result.folds))
```

---

## Combined Overfitting Assessment

Honba provides a combined assessment in `honba-ya-enhancer`:

```rust
use honba_ya_enhancer::OverfittingAssessment;

let assessment = OverfittingAssessment::assess(
    sharpe,           // Observed Sharpe
    returns,          // Return series for skew/kurtosis
    is_fitness,       // In-sample fitness per fold
    oos_fitness,      // Out-of-sample fitness per fold
);

if assessment.passes_guards {
    println!("✅ Strategy passes overfitting guards");
} else {
    println!("❌ {}", assessment.recommendation);
}
```

### Gate Thresholds (Honba Defaults)

| Metric | Pass Threshold | Rationale |
|--------|----------------|-----------|
| DSR | ≥ 3.0 | Per López de Prado, DSR ≥ 3 ≈ p < 0.001 after multiple testing |
| PBO | < 0.5 | Less than half of CSCV partitions show degradation |

Both gates must pass for a strategy to be considered credible.

---

## Practical Guidelines

### Counting `n_trials` Correctly

**Wrong**: GA population × generations (e.g., 50 × 50 = 2500)
**Right**: Number of *unique* parameter combinations evaluated

If you test 500 unique parameter sets, `n_trials = 500`, not 2500.

For Bayesian optimization with 100 evaluations: `n_trials = 100`.

For walk-forward with 8 folds × 3 parameter sets: `n_trials = 24`.

### Minimum Data Requirements

| Diagnostic | Minimum Periods | Minimum Folds |
|------------|-----------------|---------------|
| DSR | 30 (for skew/kurtosis) | N/A |
| PBO (CSCV) | N/A | 4 folds (16 partitions) |

With < 30 periods, DSR falls back to observed Sharpe (no adjustment).

### Synthetic Data vs Real Data

The examples use synthetic bars (`honba_examples.synthetic.oscillating_bars`)
for deterministic, offline testing. For real analysis:

1. Ensure `honba-data` lakehouse has the required symbols/date ranges
2. Run without `--synthetic` flag
3. Use sufficient date range: at least 2+ years for walk-forward

---

## References

1. Bailey, D. H., & López de Prado, M. (2014). *The Deflated Sharpe Ratio: Correcting
   for Selection Bias, Backtest Overfitting and Non-Normality*. Journal of Portfolio
   Management, 40(5), 94–107.

2. López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley.
   Chapter 14: Backtest Overfitting.

3. Honba `honba-ya-enhancer` crate: `overfitting_guardrails.rs`
4. Honba Python: `honba.algo_analytics.deflated_sharpe`