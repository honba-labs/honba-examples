# Optimizer Examples

Parameter search and overfitting diagnostics for Honba strategies.

## Overview

These examples demonstrate three key stages of strategy validation:

1. **Genetic Algorithm (GA) Optimization** — Multi-objective search across parameter space
2. **Bayesian Optimization (TPE)** — Efficient search using probabilistic models
3. **Overfitting Diagnostics (DSR/PBO)** — Statistical guards against data mining

## Quick Reference

| Example | Algorithm | Strategy Type | Key Outputs |
|---------|-----------|---------------|-------------|
| `01_ga_trend_breakout.py` | GA (tournament selection, crossover, mutation) | Donchian breakout | Pareto frontier (Sharpe vs DD), best params |
| `02_bayesian_mean_reversion.py` | TPE (Tree-structured Parzen Estimators) | RSI mean-reversion | Best params, evaluation history |
| `03_deflated_sharpe_analysis.py` | DSR (Bailey & López de Prado) + PBO (CSCV) | N/A (post-analysis) | DSR, PBO, combined verdict |

## Running

```bash
# GA optimization (50 generations × 50 population = 2500 evaluations)
python examples/optimizer/01_ga_trend_breakout.py --generations 50 --population 50

# Bayesian optimization (100 trials, 20 initial random)
python examples/optimizer/02_bayesian_mean_reversion.py --trials 100 --initial-points 20

# Overfitting diagnostics (100 trials, 5 CSCV folds)
python examples/optimizer/03_deflated_sharpe_analysis.py --trials 100 --folds 5
```

## Common Options

| Option | Description |
|--------|-------------|
| `--seed` | Random seed for reproducibility (default: 42) |
| `--out` | Write JSON output to file |
| `--bars` | Number of synthetic bars (default: 500) |

## Output Structure

All examples emit JSON with:

```json
{
  "config": { ... },
  "best_params": { ... },
  "best_fitness": ...,
  "total_evaluations": ...,
  "pareto_frontier": [...],
  "pareto_plot": "ASCII plot..."
}
```

The deflated Sharpe example adds:

```json
{
  "dsr": { "observed_sharpe": ..., "dsr": ..., "interpretation": "..." },
  "pbo": { "pbo": ..., "interpretation": "..." },
  "overall": { "passes_both": true, "verdict": "PASS - Ready for live validation" }
}
```

## Interpreting Results

### Pareto Frontier

The Pareto frontier shows non-dominated parameter combinations:
- **X-axis**: Max Drawdown (lower is better)
- **Y-axis**: Return % (higher is better)
- Points on the frontier (*) cannot be improved in one dimension without worsening the other

### DSR Thresholds

| DSR | Verdict |
|-----|---------|
| ≥ 3.0 | Robust — passes multiple testing correction |
| 1.5 – 3.0 | Marginal — sensitive to assumptions |
| < 1.5 | Likely overfit — selection bias dominates |

### PBO Thresholds

| PBO | Verdict |
|-----|---------|
| < 0.3 | Low overfitting probability |
| 0.3 – 0.5 | Elevated |
| ≥ 0.5 | High — strategy degrades OOS in majority of folds |

## Integration with Walk-Forward Analysis

The optimizer examples search *in-sample* only. Always validate chosen parameters
out-of-sample using walk-forward analysis:

```bash
# 1. Find candidate parameters with GA
python examples/optimizer/01_ga_trend_breakout.py --generations 50 --population 50

# 2. Validate with walk-forward
python examples/backtesting/04_walk_forward.py \
    --symbol RELIANCE \
    --start 2020-01-01 \
    --end 2024-12-31 \
    --fast <BEST_FAST> \
    --slow <BEST_SLOW>
```

## Pitfalls

- **Multiple testing bias**: The best in-sample result is always biased upward. DSR corrects for this.
- **No free lunch**: Optimization on synthetic data demonstrates structure, not profitability.
- **State leakage**: Each evaluation gets a fresh strategy instance — no state leaks between trials.
- **Real data**: Add `--symbol RELIANCE --start 2020-01-01 --end 2024-12-31` to use lakehouse data (requires `honba-data`).

## See Also

- [`docs/dsr_pbo.md`](../docs/dsr_pbo.md) — Full DSR/PBO theory and usage
- [`docs/wfo_efficiency.md`](../docs/wfo_efficiency.md) — Walk-forward efficiency theory
- [`examples/backtesting/04_walk_forward.py`](../backtesting/04_walk_forward.py) — Out-of-sample validation
- [`examples/backtesting/05_monte_carlo.py`](../backtesting/05_monte_carlo.py) — Start-date robustness