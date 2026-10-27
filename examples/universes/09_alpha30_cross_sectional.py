"""Example 09: cross-sectional Alpha-30 factor model with a top-decile rebalance.

Computes daily factor scores across the resolved NIFTY universe (momentum over the
prior ~252 sessions minus a realised-volatility penalty), longs the top decile by
score and rebalances every ``--rebalance-days`` sessions at the session close.

Two entry points:

* :func:`run` — synthetic, seed-driven, fully offline (``bars``, ``seed``), the
  contract used by ``tests/unit/test_09_alpha30_cross_sectional.py``:
    run(bars=252, seed=0, top_decile=0.1, rebalance_days=21) -> {
        "config": {...},
        "factors": {symbol: score},
        "rebalances": [{"date", "n_selected", "selection", "trades"}, ...],
        "trades": [...],
        "summary": {...},
    }
* :func:`run_cross_sectional` — catalog-backed, point-in-time over the Parquet
  lakehouse via zero-copy Arrow (duckdb ``read_parquet``), monthly calendar
  rebalances, :func:`main` with ``--start``/``--end``/``--out-dir`` writes
  ``run.json``.

Rebalance sizing mirrors ``honba_examples.ewr`` conventions (shared cash pool,
whole shares, per-side fee, sells before buys) and emits the same trade-record
shape, with the default fee from ``honba_examples.ewr.model.FEE_RATE``.

This is the hand-rolled, universe-scanning counterpart of 07/08: 07 backtests the
catalog ``equal_weight`` strategy at next-open fills, 08 simulates a fixed basket at
close fills, and 09 rescores the whole universe at every rebalance and longs its
top decile.

Run::

    python examples/universes/09_alpha30_cross_sectional.py --bars 252 --seed 42 --top-decile 0.1 --out /tmp/cs.json
    python examples/universes/09_alpha30_cross_sectional.py --start 2026-01-01 --end 2026-06-30 \\
        --top-decile 0.1 --out-dir /tmp/alpha30_cs

Pitfalls::

    - Observe and trade the close: factor scores are computed through the rebalance
      session's close and the basket is rebalanced at that same close (the same
      "observe and trade the close" bias as 08), so results flatter next-open fills.
      Use 07 for a Honba-native next-open number.
    - Survivorship: with a catalog, only symbols that have parquet bars are scored;
      symbols with fewer than --min-history-bars are never eligible, and the resolved
      universe is today's membership scanned over the whole window.
    - Zero-copy Arrow: the factor query reads every symbol's parquet in a single
      duckdb ``read_parquet`` call (Arrow-native; no row-by-row parsing). Symbols
      without a parquet directory are skipped silently.
    - Hand-chosen factor: the score is momentum minus a realised-volatility penalty;
      --top-decile, --rebalance-days and --fee are knobs, and sweeping them on one
      window and keeping the best is overfitting (report DSR/PBO if you sweep).
    - Costs: a flat per-side fee fraction of traded notional, not the statutory NSE
      cost stack; settlement and slippage are not modelled.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
from pathlib import Path
from typing import Any

from honba.display import Column, render_kv, render_table
from honba.domain.instrument import InstrumentId

from honba_examples.artifact import write_run_json
from honba_examples.base import HonbaExample, ts_to_date
from honba_examples.ewr.model import FEE_RATE
from honba_examples.metrics import curve_metrics, exposure_metrics
from honba_examples.synthetic import oscillating_bars
from honba_examples.universe_lists import ALPHA30_ENGINE

SCHEMA = "honba-examples/09-alpha30-cross-sectional/v1"
MOMENTUM_BARS = 252  # ~12 months of sessions; lookback adapts to available history
VOL_BARS = 60  # realised-volatility window for the vol penalty
DEFAULT_BARS = 252
DEFAULT_SEED = 0
DEFAULT_TOP_DECILE = 0.1
DEFAULT_REBALANCE_DAYS = 21
DEFAULT_INITIAL_CAPITAL = 1_000_000.0
DEFAULT_MIN_HISTORY_BARS = 1  # score whatever history exists; lookback adapts below
DEFAULT_UNIVERSE = "alpha30_engine"

__all__ = [
    "DEFAULT_BARS",
    "DEFAULT_INITIAL_CAPITAL",
    "DEFAULT_MIN_HISTORY_BARS",
    "DEFAULT_REBALANCE_DAYS",
    "DEFAULT_SEED",
    "DEFAULT_TOP_DECILE",
    "DEFAULT_UNIVERSE",
    "MOMENTUM_BARS",
    "VOL_BARS",
    "Alpha30CrossSectionalExample",
    "main",
    "print_summary",
    "run",
    "run_cross_sectional",
]


def _symbols_for(universe: str) -> list[str]:
    """Resolve the symbol list for a named universe (``alpha30_engine`` ships here)."""
    if universe == DEFAULT_UNIVERSE:
        return list(ALPHA30_ENGINE)
    raise ValueError(f"unknown universe {universe!r}; expected '{DEFAULT_UNIVERSE}'")


def _load_bars_arrow(
    catalog_dir: Path,
    symbols: list[str],
    start_date: dt.date,
    end_date: dt.date,
) -> dict[str, list[tuple[dt.date, float]]]:
    """Zero-copy Arrow read of the lakehouse: every symbol's 1D parquet in one duckdb
    ``read_parquet`` call, returned as ``{symbol: [(date, close), ...]}`` sorted by
    date. Symbols without a parquet directory map to an empty list.

    All available history is loaded (the date window is applied to *sessions*, not to the
    per-symbol bars), because factor scores at session ``d`` need momentum lookback bars
    that fall before ``start_date``. The recursive glob honors both
    ``<catalog>/<symbol>/bars.parquet`` and ``<catalog>/1D/NSE/<symbol>/<file>.parquet``
    lakehouse layouts.
    """
    import duckdb

    files: list[str] = []
    for symbol in symbols:
        sym_dir = catalog_dir / symbol
        if sym_dir.is_dir():
            files.extend(str(f) for f in sorted(sym_dir.glob("**/*.parquet")))
    if not files:
        return {symbol: [] for symbol in symbols}
    file_list = ", ".join(f"'{f}'" for f in files)
    query = f"""
    SELECT
        regexp_extract(filename, '.*/([^/]+)/[^/]+$', 1) AS symbol,
        epoch_ms(ts // 1000000)::DATE AS bar_date,
        close
    FROM read_parquet([{file_list}], filename=true)
    ORDER BY symbol, bar_date
    """
    con = duckdb.connect()
    try:
        rows = con.execute(query).fetchall()
    finally:
        con.close()
    bars_by_symbol: dict[str, list[tuple[dt.date, float]]] = {symbol: [] for symbol in symbols}
    for symbol, bar_date, close in rows:
        bars_by_symbol.setdefault(symbol, []).append((bar_date, float(close)))
    return bars_by_symbol


def _generate_synthetic_bars(
    symbols: list[str], n_bars: int, seed: int, start: dt.date = dt.date(2026, 1, 1)
) -> list[Any]:
    """Deterministic daily bars per symbol; each symbol gets its own drift so the
    cross-sectional ranking is non-trivial but fully reproducible from ``seed``."""
    bars: list[Any] = []
    for i, symbol in enumerate(symbols):
        drift = (i - (len(symbols) - 1) / 2) * 0.002
        bars.extend(
            oscillating_bars(
                symbol,
                n_bars,
                start=start,
                base=2400.0 + i * 25.0,
                amplitude=30.0,
                period=45,
                drift=drift,
                noise=4.0,
                seed=seed + i * 7 + 13,
            )
        )
    return bars


def _close_at_or_before(bars: list[tuple[dt.date, float]], d: dt.date) -> float | None:
    """Last close on or before session ``d`` (bars sorted ascending), else None."""
    close: float | None = None
    for bar_date, c in bars:
        if bar_date <= d:
            close = c
        else:
            break
    return close


def _factor_scores(
    bars_by_symbol: dict[str, list[tuple[dt.date, float]]],
    d: dt.date,
    min_history_bars: int,
) -> dict[str, float]:
    """Cross-sectional factor score per symbol as of session ``d``.

    Score = momentum (close vs ~252 sessions earlier, adapted to available history)
    minus a realised volatility penalty (sample std of the last 60 daily returns).
    Uses only bars on or before ``d`` (point-in-time).
    """
    scores: dict[str, float] = {}
    for symbol, bars in bars_by_symbol.items():
        hist = [(bd, c) for bd, c in bars if bd <= d]
        if len(hist) < min_history_bars:
            continue
        lookback = min(MOMENTUM_BARS, len(hist) - 1)
        close_d = hist[-1][1]
        close_prior = hist[-(lookback + 1)][1]
        momentum = close_d / close_prior - 1.0
        returns = [
            hist[i + 1][1] / hist[i][1] - 1.0 for i in range(len(hist) - 1) if hist[i][1] > 0
        ]
        window = returns[-VOL_BARS:]
        vol = statistics.pstdev(window) if len(window) > 1 else 0.0
        scores[symbol] = momentum - vol
    return scores


def _simulate(
    sessions: list[dt.date],
    bars_by_symbol: dict[str, list[tuple[dt.date, float]]],
    symbols: list[str],
    initial_capital: float,
    top_decile: float,
    rebalance_days: int,
    fee: float,
    min_history_bars: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, int], float]:
    """Long the top-decile factor basket, equal weight at the close, every
    ``rebalance_days`` sessions. Mirrors ``honba_examples.ewr`` conventions: a shared
    cash pool, whole shares, per-side fee on traded notional, sells before buys, and
    ewr-shaped trade records (``symbol``, ``side``, ``qty``, ``price``, ``notional``,
    ``fee``)."""
    qty: dict[str, int] = {symbol: 0 for symbol in symbols}
    cash = float(initial_capital)
    equity_curve: list[dict[str, Any]] = []
    rebalances: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []

    for i, d in enumerate(sessions):
        closes = {s: _close_at_or_before(bars_by_symbol[s], d) for s in symbols}
        active_closes = {s: c for s, c in closes.items() if c is not None}
        equity = cash + sum(qty[s] * active_closes.get(s, 0.0) for s in symbols)

        if i % rebalance_days == 0:
            scores = _factor_scores(bars_by_symbol, d, min_history_bars)
            eligible = [s for s in symbols if s in scores and active_closes.get(s)]
            n = max(1, round(len(eligible) * top_decile)) if eligible else 0
            n = min(n, len(eligible))
            selection = sorted(eligible, key=lambda s: scores[s], reverse=True)[:n] if n else []
            target_value = equity / len(selection) if selection else 0.0
            session_trades: list[dict[str, Any]] = []
            new_qty = dict(qty)

            # Sell anything outside the new selection first (cash funds the buys).
            for s in symbols:
                if s not in selection and qty[s] > 0 and active_closes.get(s):
                    dq = qty[s]
                    price = active_closes[s]
                    proceeds = dq * price * (1.0 - fee)
                    cash += proceeds
                    new_qty[s] = 0
                    session_trades.append(
                        {
                            "symbol": s,
                            "side": "sell",
                            "qty": dq,
                            "price": price,
                            "notional": dq * price,
                            "fee": dq * price * fee,
                        }
                    )

            # Equal-weight buys (and trims) into the selection.
            for s in selection:
                price = active_closes[s]
                target_qty = int((target_value * (1.0 - fee)) // price)
                dq = target_qty - qty[s]
                if dq > 0:
                    cost = dq * price * (1.0 + fee)
                    if cost <= cash:
                        cash -= cost
                        new_qty[s] = target_qty
                        session_trades.append(
                            {
                                "symbol": s,
                                "side": "buy",
                                "qty": dq,
                                "price": price,
                                "notional": dq * price,
                                "fee": dq * price * fee,
                            }
                        )
                    else:
                        affordable = int(cash // (price * (1.0 + fee)))
                        if affordable > 0:
                            cost = affordable * price * (1.0 + fee)
                            cash -= cost
                            new_qty[s] = qty[s] + affordable
                            session_trades.append(
                                {
                                    "symbol": s,
                                    "side": "buy",
                                    "qty": affordable,
                                    "price": price,
                                    "notional": affordable * price,
                                    "fee": affordable * price * fee,
                                }
                            )
                elif dq < 0:
                    dqn = -dq
                    proceeds = dqn * price * (1.0 - fee)
                    cash += proceeds
                    new_qty[s] = target_qty
                    session_trades.append(
                        {
                            "symbol": s,
                            "side": "sell",
                            "qty": dqn,
                            "price": price,
                            "notional": dqn * price,
                            "fee": dqn * price * fee,
                        }
                    )

            qty = new_qty
            trades.extend(session_trades)
            rebalances.append(
                {
                    "date": d.isoformat(),
                    "n_selected": len(selection),
                    "selection": sorted(selection),
                    "trades": len(session_trades),
                }
            )

        curve_value = cash + sum(qty[s] * active_closes.get(s, 0.0) for s in symbols)
        equity_curve.append({"date": d.isoformat(), "value": curve_value, "cash": cash})

    return equity_curve, rebalances, trades, qty, cash


def run(
    bars: int = DEFAULT_BARS,
    seed: int = DEFAULT_SEED,
    *,
    top_decile: float = DEFAULT_TOP_DECILE,
    rebalance_days: int = DEFAULT_REBALANCE_DAYS,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    universe: str = DEFAULT_UNIVERSE,
    fee: float = FEE_RATE,
    min_history_bars: int = DEFAULT_MIN_HISTORY_BARS,
    catalog_dir: Path | str | None = None,
    symbols: list[str] | None = None,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
) -> dict[str, Any]:
    """Run one top-decile cross-sectional factor backtest.

    With ``catalog_dir`` the result is :func:`run_cross_sectional` over the lakehouse;
    otherwise deterministic synthetic bars seeded by ``seed`` are used.
    """
    if not (0.0 < top_decile <= 1.0):
        raise ValueError(f"--top-decile must be in (0, 1], got {top_decile}")
    if rebalance_days < 1:
        raise ValueError(f"--rebalance-days must be >= 1, got {rebalance_days}")
    if symbols is None:
        symbols = _symbols_for(universe)

    if catalog_dir is not None:
        if start_date is None or end_date is None:
            raise ValueError("--start and --end are required when --catalog is given")
        raw = run_cross_sectional(
            Path(catalog_dir),
            symbols,
            start_date,
            end_date,
            initial_capital=initial_capital,
            top_decile=top_decile,
            fee=fee,
            min_history_bars=min_history_bars,
        )
        return {
            "config": {
                "universe": universe,
                "n_symbols": raw["n_symbols"],
                "symbols": symbols,
                "top_decile": top_decile,
                "rebalance_days": 1,
                "initial_capital": initial_capital,
                "fee": fee,
                "min_history_bars": min_history_bars,
                "seed": seed,
                "bars": 0,
            },
            "factors": raw.get("factors", {}),
            "rebalances": [
                {
                    "date": r["date"],
                    "n_selected": r["n_selected"],
                    "selection": r["selection"],
                    "trades": r["trades"],
                }
                for r in raw["rebalances"]
            ],
            "trades": raw["trades"],
            "summary": {
                "capital": initial_capital,
                "final_equity": raw["metrics"]["final_value"],
                "total_return_pct": raw["metrics"]["total_return_pct"],
                "cagr_pct": raw["metrics"]["cagr_pct"],
                "sharpe": raw["metrics"]["sharpe"],
                "max_drawdown_pct": raw["metrics"]["max_drawdown_pct"],
                "n_rebalances": raw["n_rebalances"],
                "n_trades": len(raw["trades"]),
                "turnover": raw["exposure"]["turnover"],
            },
        }

    bar_list = _generate_synthetic_bars(symbols, bars, seed)
    bars_by_symbol = {symbol: [] for symbol in symbols}
    for b in bar_list:
        bars_by_symbol.setdefault(b.instrument_id.symbol, []).append(
            (ts_to_date(b.ts), float(b.close))
        )

    sessions = sorted({bd for bars in bars_by_symbol.values() for bd, _ in bars})
    if not sessions:
        empty_summary = {
            "capital": initial_capital,
            "final_equity": initial_capital,
            "total_return_pct": 0.0,
            "cagr_pct": 0.0,
            "sharpe": 0.0,
            "max_drawdown_pct": 0.0,
            "n_rebalances": 0,
            "n_trades": 0,
            "turnover": 0.0,
        }
        config = {
            "universe": universe,
            "n_symbols": len(symbols),
            "symbols": symbols,
            "top_decile": top_decile,
            "rebalance_days": rebalance_days,
            "initial_capital": initial_capital,
            "fee": fee,
            "min_history_bars": min_history_bars,
            "seed": seed,
            "bars": bars,
        }
        return {
            "config": config,
            "factors": {},
            "rebalances": [],
            "trades": [],
            "summary": empty_summary,
        }

    equity_curve, rebalances, trades, _final_qty, _final_cash = _simulate(
        sessions,
        bars_by_symbol,
        symbols,
        initial_capital,
        top_decile,
        rebalance_days,
        fee,
        min_history_bars,
    )
    metrics = curve_metrics(equity_curve, initial_capital)
    turnover_notional = sum(t["notional"] for t in trades)
    exposure = exposure_metrics(equity_curve, turnover_notional)
    factors = _factor_scores(bars_by_symbol, sessions[-1], min_history_bars)

    summary = {
        "capital": initial_capital,
        "final_equity": metrics["final_value"],
        "total_return_pct": metrics["total_return_pct"],
        "cagr_pct": metrics["cagr_pct"],
        "sharpe": metrics["sharpe"],
        "max_drawdown_pct": metrics["max_drawdown_pct"],
        "n_rebalances": len(rebalances),
        "n_trades": len(trades),
        "turnover": exposure["turnover"],
    }
    config = {
        "universe": universe,
        "n_symbols": len(symbols),
        "symbols": symbols,
        "top_decile": top_decile,
        "rebalance_days": rebalance_days,
        "initial_capital": initial_capital,
        "fee": fee,
        "min_history_bars": min_history_bars,
        "seed": seed,
        "bars": bars,
    }
    return {
        "config": config,
        "factors": factors,
        "rebalances": rebalances,
        "trades": trades,
        "summary": summary,
    }


def run_cross_sectional(
    catalog_dir: Path,
    symbols: list[str],
    start_date: dt.date,
    end_date: dt.date,
    *,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    top_decile: float = DEFAULT_TOP_DECILE,
    fee: float = FEE_RATE,
    min_history_bars: int = DEFAULT_MIN_HISTORY_BARS,
) -> dict[str, Any]:
    """Long the monthly top-decile cross-sectional factor basket, equal weight at the
    close, and return the structured result (equity curve, rebalances, trades,
    metrics, exposure). Rebalances happen on the first session of every calendar
    month."""
    if not (0.0 < top_decile <= 1.0):
        raise ValueError(f"--top-decile must be in (0, 1], got {top_decile}")

    bars_by_symbol = _load_bars_arrow(catalog_dir, symbols, start_date, end_date)
    sessions = sorted(
        {bd for bars in bars_by_symbol.values() for bd, _ in bars if start_date <= bd <= end_date}
    )
    if not sessions:
        return {
            "equity_curve": [],
            "rebalances": [],
            "trades": [],
            "metrics": {
                "final_value": initial_capital,
                "total_return_pct": 0.0,
                "cagr_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "sharpe": 0.0,
            },
            "exposure": {"turnover": 0.0, "avg_cash_pct": 100.0},
            "final_holdings": {},
            "n_symbols": len(symbols),
            "n_with_data": 0,
            "n_rebalances": 0,
        }

    seen_months: set[tuple[int, int]] = set()
    rebalance_dates: list[dt.date] = []
    for d in sessions:
        key = (d.year, d.month)
        if key not in seen_months:
            seen_months.add(key)
            rebalance_dates.append(d)
    rebalance_set = set(rebalance_dates)

    qty: dict[str, int] = {symbol: 0 for symbol in symbols}
    cash = float(initial_capital)
    traded_notional = 0.0
    equity_curve: list[dict[str, Any]] = []
    rebalances: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []

    for d in sessions:
        closes = {s: _close_at_or_before(bars_by_symbol[s], d) for s in symbols}
        active_closes = {s: c for s, c in closes.items() if c is not None}
        equity = cash + sum(qty[s] * active_closes.get(s, 0.0) for s in symbols)

        if d in rebalance_set:
            scores = _factor_scores(bars_by_symbol, d, min_history_bars)
            eligible = [s for s in symbols if s in scores and active_closes.get(s)]
            n = max(1, round(len(eligible) * top_decile)) if eligible else 0
            n = min(n, len(eligible))
            basket = (
                set(sorted(eligible, key=lambda s: scores[s], reverse=True)[:n]) if n else set()
            )
            target_value = equity / len(basket) if basket else 0.0
            session_trades: list[dict[str, Any]] = []
            new_qty = dict(qty)

            # Sell anything outside the new basket first (cash funds the buys).
            for s in symbols:
                if s not in basket and qty[s] > 0 and active_closes.get(s):
                    dq = qty[s]
                    price = active_closes[s]
                    proceeds = dq * price * (1.0 - fee)
                    cash += proceeds
                    new_qty[s] = 0
                    session_trades.append(
                        {
                            "date": d.isoformat(),
                            "symbol": s,
                            "side": "sell",
                            "qty": dq,
                            "price": price,
                            "notional": dq * price,
                            "fee": dq * price * fee,
                        }
                    )

            # Equal-weight buys (and trims) into the basket.
            for s in basket:
                price = active_closes[s]
                target_qty = int((target_value * (1.0 - fee)) // price)
                dq = target_qty - qty[s]
                if dq > 0:
                    cost = dq * price * (1.0 + fee)
                    if cost <= cash:
                        cash -= cost
                        new_qty[s] = target_qty
                        session_trades.append(
                            {
                                "date": d.isoformat(),
                                "symbol": s,
                                "side": "buy",
                                "qty": dq,
                                "price": price,
                                "notional": dq * price,
                                "fee": dq * price * fee,
                            }
                        )
                    else:
                        affordable = int(cash // (price * (1.0 + fee)))
                        if affordable > 0:
                            cost = affordable * price * (1.0 + fee)
                            cash -= cost
                            new_qty[s] = qty[s] + affordable
                            session_trades.append(
                                {
                                    "date": d.isoformat(),
                                    "symbol": s,
                                    "side": "buy",
                                    "qty": affordable,
                                    "price": price,
                                    "notional": affordable * price,
                                    "fee": affordable * price * fee,
                                }
                            )
                elif dq < 0:
                    dqn = -dq
                    proceeds = dqn * price * (1.0 - fee)
                    cash += proceeds
                    new_qty[s] = target_qty
                    session_trades.append(
                        {
                            "date": d.isoformat(),
                            "symbol": s,
                            "side": "sell",
                            "qty": dqn,
                            "price": price,
                            "notional": dqn * price,
                            "fee": dqn * price * fee,
                        }
                    )

            qty = new_qty
            traded_notional += sum(t["notional"] for t in session_trades)
            trades.extend(session_trades)
            rebalances.append(
                {
                    "date": d.isoformat(),
                    "selection": sorted(basket),
                    "n_selected": len(basket),
                    "trades": len(session_trades),
                    "cash": cash,
                    "equity": equity,
                }
            )

        curve_value = cash + sum(qty[s] * active_closes.get(s, 0.0) for s in symbols)
        equity_curve.append({"date": d.isoformat(), "value": curve_value, "cash": cash})

    metrics = curve_metrics(equity_curve, initial_capital)
    exposure = exposure_metrics(equity_curve, traded_notional)
    factors = _factor_scores(bars_by_symbol, sessions[-1], min_history_bars)
    n_with_data = sum(1 for symbol in symbols if len(bars_by_symbol[symbol]) >= min_history_bars)
    return {
        "equity_curve": equity_curve,
        "rebalances": rebalances,
        "trades": trades,
        "metrics": metrics,
        "exposure": exposure,
        "factors": factors,
        "final_holdings": {s: q for s, q in qty.items() if q},
        "n_symbols": len(symbols),
        "n_with_data": n_with_data,
        "n_rebalances": len(rebalances),
    }


class Alpha30CrossSectionalExample(HonbaExample):
    """Catalog-backed CLI for the cross-sectional Alpha-30 factor model."""

    universe_name: str = "nifty50"
    exchange: str = "NSE"
    timeframe: str = "1D"
    start_date: dt.date = dt.date(2022, 1, 1)
    end_date: dt.date = dt.date(2025, 12, 31)
    initial_capital: float | None = DEFAULT_INITIAL_CAPITAL
    warmup_days: int = 0
    out_dir: Path = Path("output") / "09_alpha30_cross_sectional"
    date_range_args = True

    top_decile: float = DEFAULT_TOP_DECILE
    rebalance_days: int = DEFAULT_REBALANCE_DAYS
    fee: float = FEE_RATE
    min_history_bars: int = DEFAULT_MIN_HISTORY_BARS

    def load_universe(self) -> list[InstrumentId]:
        return [InstrumentId(symbol, self.exchange) for symbol in ALPHA30_ENGINE]

    def add_custom_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--top-decile",
            type=float,
            default=self.top_decile,
            help="Fraction of eligible symbols to long each rebalance (default: 0.1, top decile)",
        )
        parser.add_argument(
            "--rebalance-days",
            type=int,
            default=self.rebalance_days,
            help="Rebalance every N sessions",
        )
        parser.add_argument(
            "--fee", type=float, default=self.fee, help="Per-side fee fraction of notional"
        )
        parser.add_argument(
            "--min-history-bars",
            type=int,
            default=self.min_history_bars,
            help="Minimum bars of history before a symbol is eligible",
        )

    def run(self) -> dict[str, Any]:
        universe = self.load_universe()
        symbols = [iid.symbol for iid in universe]
        catalog_dir = self.store.data_dir / "catalog" / self.timeframe / self.exchange
        result = run_cross_sectional(
            catalog_dir,
            symbols,
            self.start_date,
            self.end_date,
            initial_capital=self.initial_capital,
            top_decile=self.top_decile,
            fee=self.fee,
            min_history_bars=self.min_history_bars,
        )
        config = {
            "strategy": "alpha30_cross_sectional",
            "universe_name": self.universe_name,
            "exchange": self.exchange,
            "timeframe": self.timeframe,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "initial_capital": self.initial_capital,
            "rebalance_days": self.rebalance_days,
            "top_decile": self.top_decile,
            "fee": self.fee,
            "min_history_bars": self.min_history_bars,
            "symbols": symbols,
        }
        coverage = {
            "symbols": symbols,
            "n_symbols": len(symbols),
            "n_with_data": result["n_with_data"],
        }
        out = write_run_json(
            schema=SCHEMA,
            config=config,
            universe=universe,
            coverage=coverage,
            run_result=result,
            bars=[],
            out_dir=self.ensure_out_dir(),
            paths={"data_dir": str(self.store.data_dir)},
        )
        print_summary(out, result)
        print(f"[Out] {self.out_dir / 'run.json'}")
        return out


def print_summary(out: dict[str, Any], result: dict[str, Any]) -> None:
    cfg, m = out["config"], result["metrics"]
    render_kv(
        [
            ("Universe", f"{cfg['universe_name']} ({len(cfg['symbols'])} symbols)"),
            ("Period", f"{cfg['start_date']} -> {cfg['end_date']}"),
            (
                "Rebalance",
                f"every {cfg['rebalance_days']} session(s), top {cfg['top_decile']:.0%}, "
                + f"{result['n_rebalances']} rebalance(s), {len(result['trades'])} trade(s)",
            ),
        ],
        title="Alpha-30 cross-sectional factor model",
    )
    rows = [
        ("Capital", f"{cfg['initial_capital']:,.2f}"),
        ("Final value", f"{m['final_value']:,.2f}"),
        ("Total return %", f"{m['total_return_pct']:+.2f}%"),
        ("CAGR %", f"{m['cagr_pct']:+.2f}%"),
        ("Max drawdown %", f"{m['max_drawdown_pct']:.2f}%"),
        ("Sharpe", f"{m['sharpe']:.3f}"),
        ("Turnover", f"{result['exposure']['turnover']:.3f}"),
    ]
    render_table(
        rows,
        [Column("metric", "Metric"), Column("value", "Value", align="right")],
    )
    print(f"run_hash {out['run_hash']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bars", type=int, default=DEFAULT_BARS, help="Synthetic bars per symbol")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Synthetic-data seed")
    parser.add_argument(
        "--top-decile", type=float, default=DEFAULT_TOP_DECILE, help="Top-decile fraction to long"
    )
    parser.add_argument(
        "--rebalance-days",
        type=int,
        default=DEFAULT_REBALANCE_DAYS,
        help="Rebalance every N sessions",
    )
    parser.add_argument(
        "--initial-capital",
        type=float,
        default=DEFAULT_INITIAL_CAPITAL,
        help="Starting capital in INR",
    )
    parser.add_argument("--universe", default=DEFAULT_UNIVERSE, help="Universe to trade")
    parser.add_argument("--fee", type=float, default=FEE_RATE, help="Per-side fee fraction")
    parser.add_argument(
        "--min-history-bars",
        type=int,
        default=DEFAULT_MIN_HISTORY_BARS,
        help="Minimum bars of history before a symbol is eligible",
    )
    parser.add_argument(
        "--catalog", type=Path, default=None, help="Parquet catalog dir (overrides synthetic bars)"
    )
    parser.add_argument(
        "--start", type=lambda s: dt.date.fromisoformat(s), default=None, help="First session"
    )
    parser.add_argument(
        "--end", type=lambda s: dt.date.fromisoformat(s), default=None, help="Last session"
    )
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON here")
    parser.add_argument(
        "--out-dir", type=Path, default=None, help="write run.json here (catalog mode)"
    )
    args = parser.parse_args(argv)

    catalog_mode = args.catalog is not None or args.start is not None or args.end is not None
    if catalog_mode:
        example = Alpha30CrossSectionalExample()
        if args.catalog is not None:
            example.data_dir = args.catalog
        if args.start is not None:
            example.start_date = args.start
        if args.end is not None:
            example.end_date = args.end
        example.top_decile = args.top_decile
        example.rebalance_days = args.rebalance_days
        example.fee = args.fee
        example.min_history_bars = args.min_history_bars
        example.initial_capital = args.initial_capital
        if args.out_dir is not None:
            example.out_dir = args.out_dir
        example.run()  # do not re-parse args (that would reset instance overrides to class defaults)
        return 0

    result = run(
        bars=args.bars,
        seed=args.seed,
        top_decile=args.top_decile,
        rebalance_days=args.rebalance_days,
        initial_capital=args.initial_capital,
        universe=args.universe,
        fee=args.fee,
        min_history_bars=args.min_history_bars,
        catalog_dir=args.catalog,
        start_date=args.start,
        end_date=args.end,
    )
    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
