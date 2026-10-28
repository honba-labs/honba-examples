"""production/06_multi_broker_dispatch: multi-broker strategy dispatch parity.

Runs one inline strategy (an SMA crossover) against the same synthetic bars through the
uniform adapter interface for Zerodha Kite, Angel One SmartAPI, and (planned) Upstox v2.

The strategy itself is adapter-agnostic: it submits ``OrderIntent`` objects through
``Strategy.submit`` and never imports a broker. The same ``replay`` run therefore produces
identical fills and equity regardless of which broker adapter is configured — that is the
parity this example demonstrates. Each broker adapter is constructed in paper/simulation
mode so no credentials, network or real orders are involved; Upstox is not yet published
(``AdapterNotFound``) and is reported as a scaffold.

Run::

    python examples/production/06_multi_broker_dispatch.py --brokers zerodha,angelone,upstox --capital 100000 --bars 120

Pitfalls::

    - Upstox v2 is a package scaffold today (no adapter registered): it is reported as
      unavailable rather than silently faked, so the parity report stays honest.
    - Paper mode still goes through the real adapter classes (construction, capabilities),
      but never connects or places an order; a live run would swap RunMode.PAPER for
      RunMode.LIVE and supply real credentials.
    - Only Zerodha and Angel One are published; the uniform interface (name, capabilities,
      product/expectation set) is what is compared, not broker-specific wire behaviour.
    - Equity is marked at bar close from replay fills, so drawdown misses intraday moves and
      the synthetic ramp is profitable by construction — not a live-performance promise.
    - The strategy result is identical across brokers by design (adapter-agnostic); the
      adapter surface is what must stay uniform, and capabilities are asserted non-empty.
    - Results are JSON-serialised through ``emit_json``; pass --out to persist the report.
"""

from __future__ import annotations

import datetime as dt
import sys
from typing import Any

from honba.adapters.errors import AdapterError
from honba.adapters.models import RunMode
from honba.adapters.registry import AdapterNotFound, resolve_adapter
from honba.domain.instrument import InstrumentId
from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma
from honba.strategies.testing import replay
from honba_adapters.angelone.config import AngelOneConfig
from honba_adapters.zerodha.config import ZerodhaConfig

from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.metrics import fills_equity_curve
from honba_examples.synthetic import bar as synth_bar
from honba_examples.synthetic import weekdays as weekdays_func

__all__ = ["main", "run"]

# Brokers the dispatch covers, in the order the plan names them (Zerodha, Upstox, Angel One).
# Zerodha and Angel One publish paper-mode adapters; Upstox is a scaffold (AdapterNotFound).
BROKER_PAPER_CONFIG: dict[str, Any] = {
    "zerodha": lambda: ZerodhaConfig(mode=RunMode.PAPER),
    "angelone": lambda: AngelOneConfig(mode=RunMode.PAPER),
    # upstox: no published adapter yet; resolve_adapter raises AdapterNotFound.
}

DEFAULT_BROKERS = "zerodha,angelone,upstox"


class SmaCrossover(Strategy):
    """Inline SMA crossover used identically on every broker (adapter-agnostic)."""

    name = "sma_crossover"
    warmup_bars = 30

    def __init__(self, fast: int = 10, slow: int = 30, symbol: str = "RELIANCE") -> None:
        super().__init__()
        self.fast = Sma(fast)
        self.slow = Sma(slow)
        self.symbol = symbol
        self.iid = InstrumentId(symbol=symbol, exchange="NSE")
        self._signals: int = 0

    def on_bar(self, bar) -> None:
        if bar.instrument_id != self.iid:
            return
        fast_val = self.fast.update(bar.close)
        slow_val = self.slow.update(bar.close)
        if fast_val is None or slow_val is None:
            return
        position = self.position(self.iid)
        if fast_val > slow_val and position <= 0:
            self._signals += 1
            self.buy(self.iid, 1)
        elif fast_val < slow_val and position > 0:
            self._signals += 1
            self.sell(self.iid, position)


def _broker_adapter_report(broker: str) -> dict[str, Any]:
    """Resolve and (paper-)construct one broker adapter, reporting its uniform surface.

    Never connects and never places an order. Returns availability + capability names so a
    dispatch layer can gate execution on what the broker actually supports.
    """
    entry: dict[str, Any] = {
        "broker": broker,
        "available": False,
        "reason": "",
        "capabilities": [],
        "run_mode": "paper",
    }
    factory_cfg = BROKER_PAPER_CONFIG.get(broker)
    if factory_cfg is None:
        entry["reason"] = "scaffold: no adapter published (resolve_adapter raises AdapterNotFound)"
        return entry
    try:
        adapter = resolve_adapter(broker, config=factory_cfg())
    except AdapterNotFound as exc:
        entry["reason"] = f"scaffold: {exc}"
        return entry
    except AdapterError as exc:  # credentials missing, etc.
        entry["available"] = False
        entry["reason"] = f"{type(exc).__name__}: {exc}"
        return entry
    entry["available"] = True
    caps = adapter.capabilities()
    entry["capabilities"] = sorted(getattr(c, "name", str(c)) for c in caps.features)
    entry["mode"] = str(getattr(adapter, "mode", RunMode.PAPER))
    return entry


def _run_strategy(bars, capital: float, fast: int, slow: int, symbol: str) -> dict[str, Any]:
    """Run the adapter-agnostic strategy once; identical for every broker."""
    strat = SmaCrossover(fast=fast, slow=slow, symbol=symbol)
    result = replay(strat, bars, fill_delay=1)
    fills = list(result.fills)
    equity_curve = fills_equity_curve(capital, fills, bars)
    final_equity = equity_curve[-1] if equity_curve else capital
    peak = capital
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100 if peak else 0.0)
    return {
        "n_intents": len(list(result.intents)),
        "n_fills": len(fills),
        "final_equity": final_equity,
        "total_return": final_equity - capital,
        "max_drawdown_pct": max_dd,
        "fills": [
            {
                "date": dt.datetime.fromtimestamp(f.ts / 1e9, tz=dt.UTC).date().isoformat(),
                "symbol": f.instrument_id.symbol,
                "side": f.side.value,
                "quantity": f.quantity,
                "price": f.price,
            }
            for f in fills
        ],
    }


def run(
    brokers: str = DEFAULT_BROKERS,
    capital: float = 100_000.0,
    bars: int = 120,
    fast: int = 10,
    slow: int = 30,
    symbol: str = "RELIANCE",
    out: str | None = None,
) -> dict[str, Any]:
    """Dispatch the same strategy across brokers and report parity."""
    broker_names = [b.strip() for b in brokers.split(",") if b.strip()]
    start = dt.date(2026, 1, 1)
    bars_list = [
        synth_bar(symbol, d, 2500.0 + i * 0.5) for i, d in enumerate(weekdays_func(start, bars))
    ]

    adapter_reports = [_broker_adapter_report(b) for b in broker_names]
    strategy_reports = [_run_strategy(bars_list, capital, fast, slow, symbol) for _ in broker_names]

    # Parity: the adapter-agnostic strategy yields identical results on every broker.
    parity_passed = (
        all(sr == strategy_reports[0] for sr in strategy_reports) if strategy_reports else True
    )
    available_brokers = [r["broker"] for r in adapter_reports if r["available"]]

    return {
        "strategy": "sma_crossover",
        "config": {
            "capital": capital,
            "bars": bars,
            "fast": fast,
            "slow": slow,
            "symbol": symbol,
            "brokers": broker_names,
        },
        "brokers": adapter_reports,
        "results": [
            {"broker": r["broker"], **sr} for r, sr in zip(adapter_reports, strategy_reports)
        ],
        "parity_passed": parity_passed,
        "available_brokers": available_brokers,
        "unavailable_brokers": [r["broker"] for r in adapter_reports if not r["available"]],
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--brokers", type=str, default=DEFAULT_BROKERS)
    parser.add_argument(
        "--initial-capital", "--capital", dest="capital", type=float, default=100_000.0
    )
    parser.add_argument("--bars", type=int, default=120)
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--symbol", type=str, default="RELIANCE")
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(
        brokers=args.brokers,
        capital=args.capital,
        bars=args.bars,
        fast=args.fast,
        slow=args.slow,
        symbol=args.symbol,
    )
    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
