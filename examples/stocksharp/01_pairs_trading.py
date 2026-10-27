"""stocksharp/01_pairs_trading: Statistical pairs trading (port of StockSharp pairs sample).

Strategy: two correlated NIFTY scrips (ICICIBANK vs HDFCBANK by default) are
modelled as a cointegrated pair.  Estimate the static hedge ratio by OLS
(y = alpha + beta * x), test stationarity of the spread with a Dickey-Fuller
style regression (Engle-Granger step 2), and trade the z-score of the spread:
enter long the spread (buy B, sell beta*A) at z < -entry_z, enter short at
z > +entry_z, exit at |z| <= exit_z.  Sized in units of the spread, not cash.

Runs offline and deterministic on a synthetic cointegrated path: a common
random walk plus an Ornstein-Uhlenbeck residual, so the cointegration gate is
exercised (it passes) while the numbers stay reproducible.  No ``Strategy``
subclass — a plain, hand-rolled simulation like the options examples.

Run::

    python examples/stocksharp/01_pairs_trading.py --symbol-a ICICIBANK --symbol-b HDFCBANK --bars 252
    python examples/stocksharp/01_pairs_trading.py --entry-z 2.0 --exit-z 0.0 --lookback 20 --seed 7 --out pairs.json

Pitfalls::

    - No statsmodels in this checkout: the Dickey-Fuller here is a no-lag,
      one-regressor approximation (MacKinnon 5% critical value ≈ -2.89 with
      constant).  Production pairs work should use statsmodels (adfuller with
      lag selection / coint) on real point-in-time data.
    - The synthetic residual is engineered to mean-revert, so the gate passes
      by construction; on real ICICIBANK/HDFCBANK history it often will not —
      skip non-cointegrated pairs rather than forcing a beta on them.
    - The hedge ratio is full-sample OLS, i.e. look-ahead if used live; a
      rolling-window or Kalman-filter ratio is the production fix.
    - Entry at +/-2 sigma and exit at 0 are arbitrary conventions; transaction
      costs, bid/ask crosses, borrow cost and short-sale restrictions are not
      modelled.
    - P&L is mark-to-market in spread units, not an executed backtest: lot
      sizes, freeze quantities and per-leg execution sequencing are ignored.
"""

from __future__ import annotations

import math
import random
import sys
from typing import Any

from honba_examples.cli import add_out_arg, emit_json, make_parser

__all__ = ["main", "run"]

DF_5PCT_WITH_CONSTANT = -2.89  # Dickey-Fuller, 5 %, regression with constant


def _simulate_pair(
    bars: int,
    seed: int = 42,
    beta: float = 0.9,
    half_life_days: float = 10.0,
    resid_vol: float = 0.5,
    walk_vol: float = 1.0,
) -> tuple[list[float], list[float]]:
    """Cointegrated pair: x random walk, y = beta*x + mean-reverting residual."""
    rng = random.Random(seed)
    phi = math.exp(-math.log(2.0) / half_life_days)  # AR(1) coefficient
    x = 1000.0
    e = 0.0
    xs, ys = [], []
    for _ in range(bars):
        x += rng.gauss(0.0, walk_vol)
        e = phi * e + rng.gauss(0.0, resid_vol)
        xs.append(x)
        ys.append(beta * x + e)
    return xs, ys


def _ols(x: list[float], y: list[float]) -> tuple[float, float]:
    """Least-squares slope/intercept of y on x (numpy-free normal equations)."""
    n = len(x)
    sx = sum(x)
    sy = sum(y)
    sxx = sum(v * v for v in x)
    sxy = sum(a * b for a, b in zip(x, y, strict=True))
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return 0.0, sy / n
    beta = (n * sxy - sx * sy) / denom
    alpha = (sy - beta * sx) / n
    return alpha, beta


def _rolling(seq: list[float], lookback: int, index: int) -> tuple[float, float]:
    """Mean and population std of seq[index-lookback+1:index+1]."""
    window = seq[max(0, index - lookback + 1) : index + 1]
    mean = sum(window) / len(window)
    var = sum((v - mean) ** 2 for v in window) / max(1, len(window) - 1)
    return mean, math.sqrt(var)


def _dickey_fuller(resid: list[float]) -> tuple[float, bool]:
    """No-lag Dickey-Fuller with constant: delta e ~ rho * e_lag + c.

    Returns (t-stat on rho, is_stationary at the 5% critical value).
    """
    y = [resid[t + 1] - resid[t] for t in range(len(resid) - 1)]
    x = resid[:-1]
    n = len(y)
    x_mean = sum(x) / n
    y_mean = sum(y) / n
    sxx = sum((v - x_mean) ** 2 for v in x)
    sxy = sum((a - x_mean) * (b - y_mean) for a, b in zip(x, y, strict=True))
    if abs(sxx) < 1e-12:
        return 0.0, False
    rho = sxy / sxx
    alpha = y_mean - rho * x_mean
    resid2 = [b - alpha - rho * a for a, b in zip(x, y, strict=True)]
    sigma2 = sum(v * v for v in resid2) / (n - 2)
    se_rho = math.sqrt(sigma2 / sxx) if sxx > 0 else float("inf")
    t_stat = rho / se_rho if se_rho > 0 else 0.0
    return t_stat, t_stat < DF_5PCT_WITH_CONSTANT


def run(
    symbol_a: str = "ICICIBANK",
    symbol_b: str = "HDFCBANK",
    bars: int = 252,
    seed: int = 42,
    entry_z: float = 2.0,
    exit_z: float = 0.0,
    lookback: int = 20,
    cointegration_bars: int | None = None,
) -> dict[str, Any]:
    """Backtest the z-score pair on the synthetic cointegrated path."""
    cointegration_bars = cointegration_bars or bars
    xs, ys = _simulate_pair(bars, seed=seed)

    # --- Step 1: hedge ratio (OLS of B on A over the cointegration sample) ---
    alpha, beta = _ols(xs[:cointegration_bars], ys[:cointegration_bars])

    # --- Step 2: stationarity of the spread (Engle-Granger) ---
    spread_full = [y - beta * x for x, y in zip(xs, ys, strict=True)]
    df_stat, cointegrated = _dickey_fuller(spread_full[:cointegration_bars])
    phi_ar = _ar1_coefficient(spread_full[:cointegration_bars])
    half_life = -math.log(2.0) / math.log(phi_ar) if 0.0 < phi_ar < 1.0 else float("inf")

    # --- Step 3: trade the z-score ---
    position = 0.0  # units of the spread: +1 long B / short beta*A, -1 reverse
    entry_price = 0.0
    pnl = 0.0
    trades: list[dict[str, Any]] = []
    z_series: list[float] = []

    for t in range(lookback, bars):
        spread = ys[t] - beta * xs[t]
        mean, std = _rolling(spread_full, lookback, t)
        z = (spread - mean) / std if std > 0 else 0.0
        z_series.append(z)

        if position == 0.0:
            if z < -entry_z:
                position = 1.0
                entry_price = spread
                trades.append(
                    {
                        "bar": t,
                        "side": "long_spread",
                        "z": z,
                        "spread": spread,
                    }
                )
            elif z > entry_z:
                position = -1.0
                entry_price = spread
                trades.append(
                    {
                        "bar": t,
                        "side": "short_spread",
                        "z": z,
                        "spread": spread,
                    }
                )
        else:
            if abs(z) <= exit_z:
                pnl += position * (spread - entry_price)
                trades.append(
                    {
                        "bar": t,
                        "side": "exit",
                        "z": z,
                        "spread": spread,
                        "trade_pnl": position * (spread - entry_price),
                    }
                )
                position = 0.0
                entry_price = 0.0
    # Mark any open position at the last spread.
    unrealised = position * (spread_full[-1] - entry_price) if position != 0.0 else 0.0

    return {
        "config": {
            "symbol_a": symbol_a,
            "symbol_b": symbol_b,
            "bars": bars,
            "seed": seed,
            "entry_z": entry_z,
            "exit_z": exit_z,
            "lookback": lookback,
        },
        "estimation": {
            "alpha": alpha,
            "beta": beta,
            "dickey_fuller_t": df_stat,
            "dickey_fuller_5pct": DF_5PCT_WITH_CONSTANT,
            "cointegrated": cointegrated,
            "ar1_phi": phi_ar,
            "half_life_days": half_life,
        },
        "spread": {
            "first": spread_full[0],
            "last": spread_full[-1],
            "mean": sum(spread_full) / len(spread_full),
            "std": math.sqrt(
                sum((v - (sum(spread_full) / len(spread_full))) ** 2 for v in spread_full)
                / len(spread_full)
            ),
        },
        "trading": {
            "trades": trades,
            "trade_count": len(trades),
            "realised_pnl": pnl,
            "unrealised_pnl": unrealised,
            "total_pnl": pnl + unrealised,
            "final_position": position,
        },
    }


def _ar1_coefficient(series: list[float]) -> float:
    """AR(1) coefficient of the series (phi in e_t = phi * e_{t-1} + eps)."""
    x = series[:-1]
    y = series[1:]
    n = len(x)
    x_mean = sum(x) / n
    y_mean = sum(y) / n
    sxx = sum((v - x_mean) ** 2 for v in x)
    sxy = sum((a - x_mean) * (b - y_mean) for a, b in zip(x, y, strict=True))
    return sxy / sxx if abs(sxx) > 1e-12 else 0.0


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--symbol-a", type=str, default="ICICIBANK")
    parser.add_argument("--symbol-b", type=str, default="HDFCBANK")
    parser.add_argument("--bars", type=int, default=252)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--entry-z", type=float, default=2.0)
    parser.add_argument("--exit-z", type=float, default=0.0)
    parser.add_argument("--lookback", type=int, default=20)
    parser.add_argument("--cointegration-bars", type=int, default=None, help="bars used for OLS/DF")
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(
        symbol_a=args.symbol_a,
        symbol_b=args.symbol_b,
        bars=args.bars,
        seed=args.seed,
        entry_z=args.entry_z,
        exit_z=args.exit_z,
        lookback=args.lookback,
        cointegration_bars=args.cointegration_bars,
    )

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
