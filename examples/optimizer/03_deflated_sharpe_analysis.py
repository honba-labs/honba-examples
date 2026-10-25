"""optimizer/03_deflated_sharpe_analysis: Deflated Sharpe Ratio (DSR) and Probability of Backtest Overfitting (PBO).

Computes Marcos López de Prado's DSR and PBO (CSCV) over the trials produced by
GA (01) and Bayesian (02) optimization runs. DSR adjusts the observed Sharpe for
multiple testing, non-normality, and sample length. PBO uses Combinatorially
Symmetric Cross-Validation to estimate overfitting probability.

Run::

    python examples/optimizer/03_deflated_sharpe_analysis.py --trials 50

Pitfalls::

    - DSR requires a minimum of ~30 return periods for reliable skew/kurtosis
      estimates. With fewer periods, DSR ≈ observed Sharpe (no adjustment).
    - n_trials should be the *actual* number of independent strategy variants
      tested, not the number of backtest runs (which may be correlated).
      GA population × generations overcounts; use unique parameter sets evaluated.
    - PBO via CSCV needs at least 4 folds (k=4 gives 16 combinations). The
      default k=5 requires 10+ folds for stable estimates.
    - PBO ≥ 0.5 suggests overfitting; DSR < 1.5 suggests overfitting; DSR ≥ 3.0
      is considered robust. Both must pass for a strategy to be credible.
    - This example generates synthetic trials with known properties to
      demonstrate the math. Replace with real optimization trial results.
    - The DSR formula here matches Bailey & López de Prado (2014) Eq. 10-12.
"""

from __future__ import annotations

import math
import random
import sys
from dataclasses import dataclass
from typing import Any

from honba.algo_analytics.deflated_sharpe import deflated_sharpe

from honba_examples.cli import add_out_arg, emit_json, make_parser

__all__ = ["main", "run"]


@dataclass(frozen=True, slots=True)
class DSROutput:
    """Result of DSR computation."""

    observed_sharpe: float
    n_trials: int
    skew: float
    kurtosis: float
    t: int
    dsr: float
    interpretation: str


@dataclass(frozen=True, slots=True)
class PBOOutput:
    """Result of PBO computation via CSCV."""

    n_folds: int
    is_sharpes: list[float]
    oos_sharpes: list[float]
    pbo: float
    interpretation: str


def compute_dsr(
    observed_sharpe: float,
    n_trials: int,
    returns: list[float] | None = None,
    skew: float | None = None,
    kurtosis: float | None = None,
    t: int | None = None,
) -> DSROutput:
    """Compute DSR with automatic skew/kurtosis estimation from returns."""
    if returns is not None and len(returns) >= 30:
        # Estimate moments from returns
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        std_dev = math.sqrt(variance) if variance > 0 else 1.0

        if std_dev > 0:
            n = len(returns)
            skew_calc = sum(((r - mean) / std_dev) ** 3 for r in returns) / n
            kurtosis_calc = sum(((r - mean) / std_dev) ** 4 for r in returns) / n
            # Unbiased corrections
            skew_calc *= n / (n - 1) / (n - 2) ** 0.5 if n > 2 else 0
            kurtosis_calc *= n * (n + 1) / ((n - 1) * (n - 2) * (n - 3)) if n > 3 else 3
        else:
            skew_calc, kurtosis_calc = 0.0, 3.0

        final_skew: float = skew if skew is not None else skew_calc
        final_kurt: float = kurtosis if kurtosis is not None else kurtosis_calc
    else:
        final_skew = skew if skew is not None else 0.0
        final_kurt = kurtosis if kurtosis is not None else 3.0

    final_t: int = t if t is not None else (len(returns) if returns is not None else 252)

    dsr = deflated_sharpe(observed_sharpe, n_trials, final_skew, final_kurt, final_t)

    if dsr >= 3.0:
        interp = (
            "Robust: DSR ≥ 3.0. Strategy likely has genuine edge after multiple testing correction."
        )
    elif dsr >= 1.5:
        interp = "Marginal: 1.5 ≤ DSR < 3.0. Some edge may exist but sensitive to assumptions."
    else:
        interp = "Likely overfit: DSR < 1.5. Observed Sharpe likely due to selection bias."

    return DSROutput(
        observed_sharpe=observed_sharpe,
        n_trials=n_trials,
        skew=final_skew,
        kurtosis=final_kurt,
        t=final_t,
        dsr=dsr,
        interpretation=interp,
    )


def compute_pbo_cscv(
    is_sharpes: list[float],
    oos_sharpes: list[float],
    n_folds: int,
) -> PBOOutput:
    """Compute PBO using Combinatorially Symmetric Cross-Validation.

    For each fold, compare IS vs OOS Sharpe. PBO = fraction of folds where
    OOS < IS (i.e., strategy degrades out of sample).
    """
    if len(is_sharpes) != n_folds or len(oos_sharpes) != n_folds:
        raise ValueError(
            f"Expected {n_folds} folds, got IS: {len(is_sharpes)}, OOS: {len(oos_sharpes)}"
        )

    # Fitness differences (OOS - IS)
    diffs = [oos - is_ for is_, oos in zip(is_sharpes, oos_sharpes)]

    # PBO = proportion of folds with overfitting signal (OOS < IS)
    overfitting_count = sum(1 for d in diffs if d < 0.0)
    pbo = overfitting_count / n_folds

    if pbo >= 0.5:
        interp = f"High overfitting probability (PBO = {pbo:.2f} ≥ 0.5). Strategy degrades OOS in majority of folds."
    elif pbo >= 0.3:
        interp = (
            f"Elevated overfitting probability (PBO = {pbo:.2f}). Some OOS degradation observed."
        )
    else:
        interp = f"Low overfitting probability (PBO = {pbo:.2f} < 0.3). Strategy generalizes well."

    return PBOOutput(
        n_folds=n_folds,
        is_sharpes=is_sharpes,
        oos_sharpes=oos_sharpes,
        pbo=pbo,
        interpretation=interp,
    )


def generate_synthetic_trials(
    n_trials: int,
    true_sharpe: float = 1.0,
    n_periods: int = 252,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Generate synthetic optimization trials for demonstration.

    Returns list of trial results with Sharpe ratios drawn from a distribution
    centered at true_sharpe with noise.
    """
    rng = random.Random(seed)
    trials = []

    for i in range(n_trials):
        # Simulate return series with given true Sharpe
        # Annual Sharpe -> daily Sharpe = annual / sqrt(252)
        daily_sr = true_sharpe / math.sqrt(252)
        daily_returns = [rng.gauss(daily_sr * 0.01, 0.01) for _ in range(n_periods)]

        # Observed Sharpe on this sample
        mean_ret = sum(daily_returns) / n_periods
        std_ret = math.sqrt(sum((r - mean_ret) ** 2 for r in daily_returns) / (n_periods - 1))
        observed_sr = (mean_ret / std_ret) * math.sqrt(252) if std_ret > 0 else 0.0

        # Add some selection bias: we only "see" trials with positive Sharpe
        # (in reality, we'd only keep and report the best ones)
        trials.append(
            {
                "trial_id": i,
                "observed_sharpe": observed_sr,
                "returns": daily_returns,
            }
        )

    return trials


def run(
    n_trials: int = 100,
    true_sharpe: float = 1.0,
    n_periods: int = 252,
    n_folds: int = 5,
    seed: int = 42,
) -> dict[str, Any]:
    # Generate synthetic trials
    trials = generate_synthetic_trials(n_trials, true_sharpe, n_periods, seed)

    # Sort by observed Sharpe (simulating selection)
    trials.sort(key=lambda t: t["observed_sharpe"], reverse=True)

    # Best observed Sharpe
    best_sharpe = trials[0]["observed_sharpe"]
    best_returns = trials[0]["returns"]

    # Compute DSR for the best trial
    dsr_result = compute_dsr(
        observed_sharpe=best_sharpe,
        n_trials=n_trials,
        returns=best_returns,
        t=n_periods,
    )

    # Simulate CSCV folds for PBO
    # Split the best trial's returns into folds
    fold_size = n_periods // (2 * n_folds)
    is_sharpes = []
    oos_sharpes = []

    for i in range(n_folds):
        is_start = i * fold_size
        is_end = is_start + fold_size
        oos_start = is_end
        oos_end = oos_start + fold_size

        is_rets = best_returns[is_start:is_end]
        oos_rets = best_returns[oos_start:oos_end]

        # Compute Sharpe for each
        def _sharpe(rets: list[float]) -> float:
            if len(rets) < 2:
                return 0.0
            m = sum(rets) / len(rets)
            s = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))
            return (m / s) * math.sqrt(252) if s > 0 else 0.0

        is_sharpes.append(_sharpe(is_rets))
        oos_sharpes.append(_sharpe(oos_rets))

    pbo_result = compute_pbo_cscv(is_sharpes, oos_sharpes, n_folds)

    # Overall assessment
    passes_both = dsr_result.dsr >= 3.0 and pbo_result.pbo < 0.5

    return {
        "config": {
            "n_trials": n_trials,
            "true_sharpe": true_sharpe,
            "n_periods": n_periods,
            "n_folds": n_folds,
            "seed": seed,
        },
        "best_trial": {
            "observed_sharpe": best_sharpe,
            "trial_id": trials[0]["trial_id"],
        },
        "dsr": {
            "observed_sharpe": dsr_result.observed_sharpe,
            "n_trials": dsr_result.n_trials,
            "skew": dsr_result.skew,
            "kurtosis": dsr_result.kurtosis,
            "t": dsr_result.t,
            "dsr": dsr_result.dsr,
            "interpretation": dsr_result.interpretation,
        },
        "pbo": {
            "n_folds": pbo_result.n_folds,
            "is_sharpes": pbo_result.is_sharpes,
            "oos_sharpes": pbo_result.oos_sharpes,
            "pbo": pbo_result.pbo,
            "interpretation": pbo_result.interpretation,
        },
        "overall": {
            "passes_dsr_guard": dsr_result.dsr >= 3.0,
            "passes_pbo_guard": pbo_result.pbo < 0.5,
            "passes_both": passes_both,
            "verdict": "PASS - Ready for live validation"
            if passes_both
            else "FAIL - Likely overfit",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--trials", type=int, default=100, help="Number of optimization trials")
    parser.add_argument(
        "--true-sharpe", type=float, default=1.0, help="True underlying Sharpe for simulation"
    )
    parser.add_argument("--periods", type=int, default=252, help="Number of return periods")
    parser.add_argument("--folds", type=int, default=5, help="CSCV folds for PBO")
    parser.add_argument("--seed", type=int, default=42)
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(
        n_trials=args.trials,
        true_sharpe=args.true_sharpe,
        n_periods=args.periods,
        n_folds=args.folds,
        seed=args.seed,
    )

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
