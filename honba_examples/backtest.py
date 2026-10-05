"""Daily multi-instrument backtest loop with a warm-up gate and a paise ledger.

``run_portfolio_backtest`` drives one core ``Strategy`` through the core
``StrategyRunner`` and ``LedgerContext``, with ``NextOpenExecution`` as the
execution port. Bars are grouped into sessions (calendar dates); per session:

1. the port opens the session: due sale proceeds settle, then orders from earlier
   sessions fill at this session's opens (sells first);
2. every bar of the session goes to the strategy (``runner.on_event``); the fills
   from step 1 are booked in the ledger after the session's first bar;
3. a test-window session is marked to its closes and appended to the curve.

Sessions before ``test_start`` are warm-up: the strategy sees their bars (so
indicators converge) but the port is gated and releases every order, so warm-up
can neither trade nor leak fills, fees or turnover into the result. A strategy
that keeps a schedule (for example "rebalance every 15 sessions") still advances
that schedule during warm-up; give such strategies no more warm-up than their
indicators need.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from itertools import groupby
from typing import Any

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.strategies.base import Strategy
from honba.strategies.context import LedgerContext
from honba.strategies.runner import StrategyRunner

from honba_examples.base import ts_to_date
from honba_examples.execution import CostFn, FillRecord, NextOpenExecution, OrderEvent
from honba_examples.metrics import curve_metrics, exposure_metrics
from honba_examples.money import delivery_cost_paise, notional_paise, paise_to_rupees

__all__ = ["BacktestRun", "canonical_hash", "run_portfolio_backtest"]


def canonical_hash(payload: Any) -> str:
    """sha256 of the canonical (sorted-key, compact) JSON form of ``payload``."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class BacktestRun:
    """Outcome of one run. Money fields are integer paise; floats are statistics only."""

    capital_paise: int
    settlement_days: int
    fills: list[FillRecord] = field(default_factory=list)
    order_events: list[OrderEvent] = field(default_factory=list)
    curve: list[dict[str, Any]] = field(default_factory=list)
    final_positions: dict[str, float] = field(default_factory=dict)
    fees_paise: int = 0
    traded_notional_paise: int = 0
    warmup_sessions: int = 0
    test_sessions: int = 0
    bars_seen: int = 0
    rejections: int = 0

    def float_curve(self) -> list[dict[str, Any]]:
        """The curve in rupees (floats) for the statistics in ``honba_examples.metrics``."""
        return [
            {
                "date": p["date"],
                "value": paise_to_rupees(p["equity_paise"]),
                "cash": paise_to_rupees(p["cash_paise"]),
            }
            for p in self.curve
        ]

    def metrics(self) -> dict[str, Any]:
        curve = self.float_curve()
        capital = paise_to_rupees(self.capital_paise)
        kinds = [e.kind for e in self.order_events]
        last = self.curve[-1]
        return {
            **curve_metrics(curve, capital),
            **exposure_metrics(curve, paise_to_rupees(self.traded_notional_paise)),
            "final_equity_paise": last["equity_paise"],
            "final_cash_paise": last["cash_paise"],
            "positions_value_paise": last["positions_value_paise"],
            "total_fees_paise": self.fees_paise,
            "total_fees": paise_to_rupees(self.fees_paise),
            "traded_notional_paise": self.traded_notional_paise,
            "n_fills": len(self.fills),
            "n_suppressed_warmup": kinds.count("suppressed_warmup"),
            "n_released_unfunded": kinds.count("released_unfunded"),
            "n_released_no_position": kinds.count("released_no_position"),
            "n_unfilled_at_end": kinds.count("unfilled_at_end"),
            "n_rejected_intents": self.rejections,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "capital_paise": self.capital_paise,
            "settlement_days": self.settlement_days,
            "warmup_sessions": self.warmup_sessions,
            "test_sessions": self.test_sessions,
            "bars_seen": self.bars_seen,
            "metrics": self.metrics(),
            "final_positions": dict(sorted(self.final_positions.items())),
            "fills": [asdict(f) for f in self.fills],
            "order_events": [asdict(e) for e in self.order_events],
            "equity_curve": self.curve,
        }


def run_portfolio_backtest(
    strategy: Strategy,
    bars: Sequence[Bar],
    *,
    test_start: dt.date,
    test_end: dt.date,
    capital_paise: int,
    settlement_days: int,
    cost_paise: CostFn = delivery_cost_paise,
) -> BacktestRun:
    """Run ``strategy`` over ``bars``; trade and account only within ``[test_start, test_end]``."""
    if test_start > test_end:
        raise ValueError(f"test_start {test_start} is after test_end {test_end}")
    ordered = sorted(
        (b for b in bars if ts_to_date(b.ts) <= test_end),
        key=lambda b: (b.ts, b.instrument_id.symbol, b.instrument_id.exchange),
    )
    sessions = [
        (day, list(group)) for day, group in groupby(ordered, key=lambda b: ts_to_date(b.ts))
    ]
    if not any(day >= test_start for day, _ in sessions):
        raise ValueError(f"no bars between {test_start} and {test_end}")

    ctx = LedgerContext(cash=paise_to_rupees(capital_paise))
    port = NextOpenExecution(
        cash_paise=capital_paise,
        settlement_days=settlement_days,
        release=ctx.release,
        cost_paise=cost_paise,
        trading=False,
    )
    runner = StrategyRunner(strategy, port, ctx=ctx)
    run = BacktestRun(capital_paise=capital_paise, settlement_days=settlement_days)
    closes: dict[InstrumentId, float] = {}

    runner.start()
    for day, session_bars in sessions:
        in_test = day >= test_start
        if in_test and not port.trading_enabled:
            port.enable_trading()
        port.open_session(day, session_bars)
        for b in session_bars:
            runner.on_event(b, b.ts)
            closes[b.instrument_id] = b.close
        run.bars_seen += len(session_bars)
        if not in_test:
            run.warmup_sessions += 1
            continue
        run.test_sessions += 1
        positions_value = sum(
            notional_paise(qty, closes[iid]) for iid, qty in port.positions.items()
        )
        run.curve.append(
            {
                "date": day.isoformat(),
                "equity_paise": port.cash_paise + positions_value,
                "cash_paise": port.cash_paise,
                "available_cash_paise": port.available_cash_paise,
                "positions_value_paise": positions_value,
            }
        )
    runner.stop()
    port.finish()

    run.fills = list(port.fill_records)
    run.order_events = list(port.order_events)
    run.fees_paise = port.fees_paise
    run.traded_notional_paise = port.traded_notional_paise
    run.final_positions = {iid.symbol: qty for iid, qty in port.positions.items()}
    run.rejections = len(runner.rejections)
    return run
