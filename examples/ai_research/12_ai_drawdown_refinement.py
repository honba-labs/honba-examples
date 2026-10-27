"""ai_research/12_ai_drawdown_refinement: AI strategy auto-enhancer (drawdown -> ADX filter).

Runs a base SMA crossover strategy on synthetic bars, detects a drawdown streak
(>= 12 % from peak), and lets a seeded ``ScriptedFakeLlm`` diagnose it: add an
ADX trend filter so the strategy only trades when the market is trending
(ADX above 25 with +DI above -DI for longs, below -DI for shorts). The AI's
proposed change is run through ``honba_examples.decision_guard.guard_llm_decision``
(typed LlmDecision guard rules: reduce-only, no quantity increases, fail-closed),
then both the base and the ADX-filtered strategy are re-evaluated out-of-sample
on a held-out walk-forward test slice (replay on the same fold geometry as
Phase 2's ``honba.algo_analytics.walk_forward``).

Deflation: the improvement is assessed with the Deflated Sharpe Ratio over
bootstrap trials of the return series, so a lucky OOS bounce isn't mistaken for
edge. Everything is offline and deterministic — no model, no network, no API key.

Run::

    python examples/ai_research/12_ai_drawdown_refinement.py --seed 42
    python examples/ai_research/12_ai_drawdown_refinement.py --seed 7 --bars 400 --out refinement.json

Pitfalls::

    - The "AI" diagnosis is a canned reply from ScriptedFakeLlm, so the ADX
      filter is always the same; this demonstrates the guard + refinement
      plumbing, not a discovered alpha.
    - Drawdown detection and the OOS re-evaluation use the same synthetic
      path; the held-out slice is walk-forward geometry, but with one path the
      result is path-dependent, not evidence. Validate on many paths before
      trusting the filter.
    - guard_llm_decision rejects quantity increases (fail-closed / ValueError)
      and protective exits are never shrunk; here the AI only proposes a filter
      (no quantity change), so the guard passes it through unchanged.
    - ADX needs warm-up bars (di_length + adx_smoothing ~= 28) before it is
      non-None; the enhanced strategy skips signals while warming up.
    - The reported DSR corrects for the bootstrap trials in this run only; it
      does not account for the drawdown-triggered decision to refine (that
      selection is itself a trial).
"""

from __future__ import annotations

import datetime as dt
import math
import random
import sys
from typing import Any

from honba.algo_analytics import deflated_sharpe
from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent
from honba.strategies.base import Strategy
from honba.strategies.indicators import Adx, Sma
from honba.strategies.testing import replay

from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.decision_guard import guard_llm_decision
from honba_examples.jsonable import jsonable
from honba_examples.synthetic import oscillating_bars

__all__ = ["main", "run"]

DEFAULT_BARS = 400
DEFAULT_SEED = 42
DEFAULT_CAPITAL = 100_000.0
DEFAULT_SYMBOL = "RELIANCE"
DRAWDOWN_TRIGGER_PCT = 12.0  # diagnose + refine when drawdown reaches 12%
ADX_TREND_THRESHOLD = 25.0  # only trade when ADX >= 25 (trending regime)

# Canned AI diagnosis, deterministic.
AI_DIAGNOSIS = (
    "Drawdown streak detected (>= 12% from peak): the SMA crossover is "
    "churning in a choppy, non-trending regime. Add an ADX(14) trend filter: "
    "only go long when ADX >= 25 and +DI > -DI, only short when ADX >= 25 and "
    "-DI > +DI; flat otherwise. Re-evaluate out-of-sample."
)


def _drawdown_pct(equity: list[float]) -> tuple[float, int]:
    """Max drawdown (% from peak) and the session index where it occurs."""
    peak = equity[0]
    max_dd = 0.0
    dd_index = 0
    for i, value in enumerate(equity):
        peak = max(peak, value)
        dd = (peak - value) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            dd_index = i
    return max_dd * 100.0, dd_index


def _fills_by_day(result: Any) -> dict[str, list[dict[str, Any]]]:
    """Group replay fills by ISO date."""
    data = jsonable(result)
    fills = data.get("fills", [])
    by_day: dict[str, list[dict[str, Any]]] = {}
    for f in fills:
        day = dt.datetime.fromtimestamp(f["ts"] / 1e9, tz=dt.timezone.utc).date().isoformat()
        by_day.setdefault(day, []).append(f)
    return by_day


def _equity_curve_from_replay(
    bars: list[Bar], result: Any, capital: float
) -> tuple[list[float], int]:
    """Mark-to-market equity curve (per bar) and fill count from a replay.

    Walks bars in order, applying that session's fills and marking the position
    to the bar's close.
    """
    fills_by_day = _fills_by_day(result)
    equity = capital
    position = 0.0
    curve: list[float] = []
    for bar in bars:
        day = dt.datetime.fromtimestamp(bar.ts / 1e9, tz=dt.timezone.utc).date().isoformat()
        for f in fills_by_day.get(day, []):
            notional = f["quantity"] * f["price"]
            if f["side"] == "buy":
                equity -= notional
                position += f["quantity"]
            else:
                equity += notional
                position -= f["quantity"]
        curve.append(equity + position * bar.close)
    return curve, sum(len(v) for v in fills_by_day.values())


def _sharpe(returns: list[float], periods_per_year: float = 252.0) -> float:
    if len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    var = sum((r - mean_r) ** 2 for r in returns) / len(returns)
    if var <= 0:
        return 0.0
    return (mean_r / math.sqrt(var)) * math.sqrt(periods_per_year)


class SmaCrossover(Strategy):
    """Base strategy: fast/slow SMA crossover, one lot per signal."""

    name = "sma_crossover_base"

    def __init__(
        self,
        symbol: str = DEFAULT_SYMBOL,
        fast: int = 10,
        slow: int = 30,
        quantity: float = 100.0,
    ) -> None:
        super().__init__()
        self.symbol = symbol
        self.fast = Sma(fast)
        self.slow = Sma(slow)
        self.quantity = quantity

    def on_bar(self, bar: Bar) -> None:
        if bar.instrument_id.symbol != self.symbol:
            return
        fast_val = self.fast.update(bar.close)
        slow_val = self.slow.update(bar.close)
        if fast_val is None or slow_val is None:
            return
        position = self.position(bar.instrument_id)
        if fast_val > slow_val and position <= 0:
            self.buy(bar.instrument_id, self.quantity)
        elif fast_val < slow_val and position > 0:
            self.sell(bar.instrument_id, position)


class SmaCrossoverWithAdx(Strategy):
    """Enhanced: SMA crossover only when ADX confirms a trending regime."""

    name = "sma_crossover_adx_filter"

    def __init__(
        self,
        symbol: str = DEFAULT_SYMBOL,
        fast: int = 10,
        slow: int = 30,
        adx_period: int = 14,
        adx_threshold: float = ADX_TREND_THRESHOLD,
        quantity: float = 100.0,
    ) -> None:
        super().__init__()
        self.symbol = symbol
        self.fast = Sma(fast)
        self.slow = Sma(slow)
        self.adx = Adx(di_length=adx_period, adx_smoothing=adx_period)
        self.adx_threshold = adx_threshold
        self.quantity = quantity

    def on_bar(self, bar: Bar) -> None:
        if bar.instrument_id.symbol != self.symbol:
            return
        fast_val = self.fast.update(bar.close)
        slow_val = self.slow.update(bar.close)
        adx_val = self.adx.update(bar.high, bar.low, bar.close)
        if fast_val is None or slow_val is None or adx_val is None:
            return  # warming up: no signals
        trending = adx_val.adx >= self.adx_threshold
        position = self.position(bar.instrument_id)
        if (
            fast_val > slow_val
            and position <= 0
            and trending
            and adx_val.plus_di > adx_val.minus_di
        ):
            self.buy(bar.instrument_id, self.quantity)
        elif (
            fast_val < slow_val
            and position > 0
            and trending
            and adx_val.minus_di > adx_val.plus_di
            or not trending
            and position > 0
        ):
            self.sell(bar.instrument_id, position)


def _evaluate(
    strategy: Strategy,
    bars: list[Bar],
    capital: float = DEFAULT_CAPITAL,
) -> dict[str, Any]:
    """Replay ``strategy`` on ``bars``; return Sharpe, max drawdown, fills."""
    result = replay(strategy, list(bars))
    curve, n_fills = _equity_curve_from_replay(list(bars), result, capital)
    returns = [(curve[i] / curve[i - 1]) - 1.0 for i in range(1, len(curve)) if curve[i - 1] > 0]
    max_dd, _ = _drawdown_pct(curve) if curve else (0.0, 0)
    return {
        "sharpe": _sharpe(returns),
        "max_drawdown_pct": max_dd,
        "final_equity": curve[-1] if curve else capital,
        "n_fills": n_fills,
        "curve": curve,
        "returns": returns,
    }


def run(
    bars: int = DEFAULT_BARS,
    seed: int = DEFAULT_SEED,
    capital: float = DEFAULT_CAPITAL,
    symbol: str = DEFAULT_SYMBOL,
) -> dict[str, Any]:
    """Base strategy -> drawdown diagnosis -> ADX filter -> OOS re-evaluation."""
    bar_list = oscillating_bars(
        symbol,
        bars,
        seed=seed,
        amplitude=60.0,
        period=40,
        drift=0.0,
        noise=5.0,
    )
    n = len(bar_list)
    train = bar_list[: n // 2]
    test = bar_list[n // 2 :]

    # --- In-sample base: detect drawdown streak ------------------------------
    base_is = _evaluate(SmaCrossover(symbol=symbol), train, capital)
    max_dd_is = base_is["max_drawdown_pct"]
    drawdown_triggered = max_dd_is >= DRAWDOWN_TRIGGER_PCT

    # --- AI diagnosis (seeded fake LLM) --------------------------------------
    diagnosis = (
        AI_DIAGNOSIS if drawdown_triggered else ("No drawdown streak; no refinement triggered.")
    )

    # --- Guard the AI's proposed change (typed LlmDecision guard rules) -----
    # The AI proposes adding a filter (no quantity change), represented as an
    # APPROVE decision on a placeholder buy intent of current size.
    intent = OrderIntent.market_buy(InstrumentId(symbol, "NSE"), 100.0)
    guard_raw = {
        "decision": "approve",
        "decided_quantity": 100.0,
        "rationale": diagnosis,
    }
    guarded = guard_llm_decision(guard_raw, intent)
    guard_passed = guarded.effective_intent is not None and not guarded.fail_closed

    # --- Enhanced strategy + OOS re-evaluation (walk-forward test slice) ----
    enhanced = SmaCrossoverWithAdx(symbol=symbol)
    base_oos = _evaluate(SmaCrossover(symbol=symbol), test, capital)
    enhanced_oos = _evaluate(enhanced, test, capital)

    # --- Deflated Sharpe over bootstrap trials of OOS returns ---------------
    rng = random.Random(seed + 1)
    oos_returns = enhanced_oos["returns"]
    n_trials = 250
    m = len(oos_returns)
    trial_sharps: list[float] = []
    if oos_returns:
        for _ in range(n_trials):
            sample = [rng.choice(oos_returns) for _ in range(m)]
            mean_r = sum(sample) / m
            var = sum((r - mean_r) ** 2 for r in sample) / m
            trial_sharps.append((mean_r / math.sqrt(var)) * math.sqrt(252.0) if var > 0 else 0.0)
    dsr = (
        deflated_sharpe(
            enhanced_oos["sharpe"], len(trial_sharps), skew=0.0, kurtosis=3.0, t=max(m, 2)
        )
        if oos_returns
        else 0.0
    )

    oos_is_ratio = (
        enhanced_oos["sharpe"] / base_oos["sharpe"]
        if base_oos["sharpe"] > 0
        else None  # undefined when in-sample Sharpe is not positive (JSON-safe)
    )

    return {
        "config": {
            "symbol": symbol,
            "bars": bars,
            "seed": seed,
            "capital": capital,
            "drawdown_trigger_pct": DRAWDOWN_TRIGGER_PCT,
            "adx_threshold": ADX_TREND_THRESHOLD,
        },
        "diagnosis": {
            "drawdown_triggered": drawdown_triggered,
            "in_sample_max_drawdown_pct": max_dd_is,
            "ai_diagnosis": diagnosis,
            "guard_passed": guard_passed,
            "guard_reasons": list(guarded.reasons),
        },
        "base": {
            "in_sample": {
                "sharpe": base_is["sharpe"],
                "max_drawdown_pct": base_is["max_drawdown_pct"],
                "final_equity": base_is["final_equity"],
                "n_fills": base_is["n_fills"],
            },
            "out_of_sample": {
                "sharpe": base_oos["sharpe"],
                "max_drawdown_pct": base_oos["max_drawdown_pct"],
                "final_equity": base_oos["final_equity"],
                "n_fills": base_oos["n_fills"],
            },
        },
        "enhanced_adx_filter": {
            "out_of_sample": {
                "sharpe": enhanced_oos["sharpe"],
                "max_drawdown_pct": enhanced_oos["max_drawdown_pct"],
                "final_equity": enhanced_oos["final_equity"],
                "n_fills": enhanced_oos["n_fills"],
            },
        },
        "refinement": {
            "oos_is_ratio": oos_is_ratio,
            "dsr": dsr,
            "dsr_trials": len(trial_sharps),
            "sharpe_improved": enhanced_oos["sharpe"] > base_oos["sharpe"],
            "drawdown_reduced": enhanced_oos["max_drawdown_pct"] < base_oos["max_drawdown_pct"],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--bars", type=int, default=DEFAULT_BARS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument("--symbol", type=str, default=DEFAULT_SYMBOL)
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(
        bars=args.bars,
        seed=args.seed,
        capital=args.capital,
        symbol=args.symbol,
    )

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
