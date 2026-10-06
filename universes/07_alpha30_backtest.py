"""Example 07: Honba-native backtest of the Alpha-30 equal-weight catalog strategy.

This is the reference way to backtest a catalog strategy over a universe in Honba.
(08 is the separate Jesse-parity port, kept for engine-to-engine comparison.)

Pipeline
--------
1. **Strategy** - ``alpha30_equal_weight`` is loaded from the ``honba-strategies``
   catalog by registry name (``--strategies-dir`` / ``$HONBA_STRATEGIES_DIR`` /
   sibling checkout) with its ``config.toml``.
2. **Universe** - resolved once, from the universe the strategy trades
   (``nifty200_alpha_30``). The same list is loaded from the Parquet store; members
   with no bars in the test window are reported (``--require-full-coverage`` makes
   that fatal). The strategy sizes over the whole universe, so a missing member's
   share simply stays in cash.
3. **Run** - the core ``StrategyRunner`` and ``LedgerContext`` drive the strategy.
   Orders fill at the *next session's open* (never the close that produced them),
   sells before buys, with NSE delivery costs (``nse_equity_delivery_cost`` legs,
   each rounded to paise) and the exchange's settlement cycle from the engine
   (``settlement_days_for``: T+2 on NSE): a buy waits for sale proceeds to settle
   and is cut to the cash available.
4. **Warm-up** - ``--warmup-days`` of bars before ``--test-start`` are fed to the
   strategy for its indicators but cannot trade; only test-window fills, fees and
   turnover count. Alpha-30 has no indicators and keeps a 15-session rebalance
   clock that warm-up would advance, so its default warm-up is 0.
5. **Output** - money is integer paise (ADR 0011). ``--out-dir/run.json`` holds the
   config, universe, data coverage, metrics, fills, order events, equity curve and
   hashes of the inputs and results; a summary prints to stdout.

The test window is explicit: ``--test-start`` defaults to 2026-01-01 and
``--test-end`` defaults to the latest bar in the store at run time (recorded in
run.json as ``test_end_source``).

Run::

    python universes/07_alpha30_backtest.py --test-start 2026-06-01 --test-end 2026-09-20 \\
        --out-dir /tmp/alpha30
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.markets.india.settlement import settlement_days_for
from honba.markets.india.universes import resolve_universe

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:  # plain checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba_examples.backtest import BacktestRun, canonical_hash, run_portfolio_backtest
from honba_examples.base import HonbaExample, ts_to_date
from honba_examples.catalog import CatalogError, find_catalog, load_catalog_strategy
from honba_examples.money import paise_to_rupees, to_paise

SCHEMA = "honba-examples/07-alpha30-backtest/v1"
DEFAULT_STRATEGY = "alpha30_equal_weight"


def _date(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def bars_digest(bars: list[Bar]) -> str:
    """sha256 over every loaded bar, so a run hash changes when the data does."""
    h = hashlib.sha256()
    for b in sorted(bars, key=lambda b: (b.instrument_id.symbol, b.ts)):
        h.update(
            f"{b.instrument_id.symbol}|{b.instrument_id.exchange}|{b.ts}|{b.open!r}|{b.high!r}|"
            f"{b.low!r}|{b.close!r}|{b.volume!r}\n".encode()
        )
    return h.hexdigest()


def data_coverage(
    universe: list[InstrumentId], bars: list[Bar], test_start: dt.date
) -> dict[str, Any]:
    """Per-member bar counts (warm-up / test window) and the members with no test bars."""
    per: dict[str, dict[str, Any]] = {
        iid.symbol: {"bars_warmup": 0, "bars_test": 0, "first": None, "last": None}
        for iid in universe
    }
    for b in bars:
        row = per[b.instrument_id.symbol]
        day = ts_to_date(b.ts).isoformat()
        row["bars_test" if day >= test_start.isoformat() else "bars_warmup"] += 1
        row["first"] = min(row["first"] or day, day)
        row["last"] = max(row["last"] or day, day)
    missing = sorted(s for s, row in per.items() if row["bars_test"] == 0)
    return {"members": per, "missing": missing, "n_members": len(per), "n_missing": len(missing)}


class Alpha30BacktestExample(HonbaExample):
    """Honba-native Alpha-30 equal-weight backtest (next-open fills, T+2, paise ledger)."""

    universe_name: str = "nifty200_alpha_30"
    initial_capital: float | None = None  # None: the strategy config's `capital`
    warmup_days: int = 0
    out_dir: Path = Path("output") / "07_alpha30"
    date_range_args = False  # the window is --test-start/--test-end

    test_start: dt.date = dt.date(2026, 1, 1)
    test_end: dt.date | None = None  # None: latest bar in the store, resolved at run time
    strategy: str = DEFAULT_STRATEGY
    strategies_dir: Path | None = None
    settlement_days: int | None = None
    require_full_coverage: bool = False

    def add_custom_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--test-start", type=_date, default=self.test_start)
        parser.add_argument(
            "--test-end",
            type=_date,
            default=None,
            help="Last test session (default: latest bar in the store, recorded in run.json)",
        )
        parser.add_argument("--strategy", default=self.strategy, help="Catalog registry name")
        parser.add_argument(
            "--strategies-dir",
            type=Path,
            default=None,
            help="honba-strategies checkout (default: $HONBA_STRATEGIES_DIR, then sibling repo)",
        )
        parser.add_argument(
            "--settlement-days",
            type=int,
            default=None,
            help="Sessions until sale proceeds are spendable (default: engine value for the "
            "exchange, T+2 on NSE)",
        )
        parser.add_argument(
            "--require-full-coverage",
            action="store_true",
            help="Fail if any universe member has no bars in the test window",
        )

    # -- steps -------------------------------------------------------------------
    def resolve(self, universe_key: str | None) -> list[InstrumentId]:
        universe = resolve_universe(self.universe_name, exchange=self.exchange)
        if universe_key:
            # Compare resolved members, not names: the catalog spells the key
            # "nifty200_alpha30" while aliases such as "nifty200_alpha_30" also
            # resolve to the same basket, and both must be accepted.
            strategy_universe = resolve_universe(universe_key, exchange=self.exchange)
            if set(strategy_universe) != set(universe):
                raise SystemExit(
                    f"--universe {self.universe_name!r} differs from the universe the strategy "
                    f"trades ({universe_key!r}); the loader and the strategy must agree"
                )
        return sorted(set(universe), key=lambda i: (i.symbol, i.exchange))

    def load(self, universe: list[InstrumentId], start: dt.date, end: dt.date) -> list[Bar]:
        bars: list[Bar] = []
        for iid in universe:
            bars.extend(self.load_bars([iid], start, end))
        return sorted(bars, key=lambda b: (b.ts, b.instrument_id.symbol))

    def run(self) -> dict[str, Any]:
        try:
            catalog = find_catalog(self.strategies_dir)
            loaded = load_catalog_strategy(self.strategy, catalog)
        except CatalogError as exc:
            raise SystemExit(str(exc)) from exc
        cfg = loaded.config

        universe = self.resolve(getattr(loaded.module, "UNIVERSE_KEY", None))
        capital = (
            self.initial_capital
            if self.initial_capital is not None
            else float(cfg.params.get("capital", 1_000_000))
        )
        if capital <= 0:
            raise SystemExit("--capital must be positive")
        if self.settlement_days is not None:
            settlement, settlement_source = self.settlement_days, "cli"
        elif cfg.settlement_days is not None:
            settlement, settlement_source = int(cfg.settlement_days), "strategy_config"
        else:
            settlement = settlement_days_for(self.exchange)
            settlement_source = f"engine:settlement_days_for({self.exchange})"

        # Window: explicit start; end explicit or the latest bar on/before today.
        warmup_start = self.test_start - dt.timedelta(days=self.warmup_days)
        if self.test_end is not None:
            test_end, end_source = self.test_end, "cli"
            bars = self.load(universe, warmup_start, test_end)
        else:
            today = dt.datetime.now(dt.timezone.utc).date()  # resolved at run time, recorded
            bars = self.load(universe, warmup_start, today)
            if not bars:
                raise SystemExit(f"no bars on or after {warmup_start} in {self.store.data_dir}")
            test_end, end_source = ts_to_date(bars[-1].ts), "latest_bar"
        if test_end < self.test_start:
            raise SystemExit(f"--test-end {test_end} is before --test-start {self.test_start}")

        coverage = data_coverage(universe, bars, self.test_start)
        if coverage["n_missing"] == len(universe):
            raise SystemExit(
                f"no bars for any of {len(universe)} members between {self.test_start} and "
                f"{test_end} in {self.store.data_dir}"
            )
        if coverage["missing"]:
            msg = (
                f"{coverage['n_missing']}/{len(universe)} members have no bars in the test "
                f"window: {', '.join(coverage['missing'])}"
            )
            if self.require_full_coverage:
                raise SystemExit(msg)
            print(f"[Data] WARNING {msg}; their equal-weight share stays in cash")

        strategy = loaded.cls(cfg)
        result = run_portfolio_backtest(
            strategy,
            bars,
            test_start=self.test_start,
            test_end=test_end,
            capital_paise=to_paise(capital),
            settlement_days=settlement,
        )

        config = {
            "strategy": loaded.name,
            "strategy_source_sha256": loaded.source_sha256,
            "strategy_params": dict(sorted(cfg.params.items())),
            "universe_name": self.universe_name,
            "exchange": self.exchange,
            "timeframe": self.timeframe,
            "test_start": self.test_start.isoformat(),
            "test_end": test_end.isoformat(),
            "test_end_source": end_source,
            "warmup_days": self.warmup_days,
            "warmup_start": warmup_start.isoformat(),
            "capital_paise": to_paise(capital),
            "settlement_days": settlement,
            "settlement_source": settlement_source,
            "fill_model": "next_session_open",
            "cost_model": "nse_equity_delivery, per-leg rounded to paise",
        }
        input_hash = canonical_hash(
            {"config": config, "universe": [i.symbol for i in universe], "bars": bars_digest(bars)}
        )
        body = result.to_dict()
        result_hash = canonical_hash(body)
        out = {
            "schema": SCHEMA,
            "config": config,
            "universe": [i.symbol for i in universe],
            "data_coverage": coverage,
            "result": body,
            "input_hash": input_hash,
            "result_hash": result_hash,
            "run_hash": canonical_hash([input_hash, result_hash]),
            # Paths are provenance only and stay out of every hash.
            "paths": {"data_dir": str(self.store.data_dir), "strategy_dir": str(loaded.path)},
        }
        path = self.ensure_out_dir() / "run.json"
        path.write_text(json.dumps(out, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        print_summary(out, result)
        print(f"[Out] {path}")
        return out


def print_summary(out: dict[str, Any], result: BacktestRun) -> None:
    cfg, m = out["config"], out["result"]["metrics"]
    rupees = paise_to_rupees
    print("=" * 64)
    print(f"Alpha-30 equal weight  {cfg['test_start']} -> {cfg['test_end']}  ({cfg['exchange']})")
    print(
        f"sessions: {result.test_sessions} test, {result.warmup_sessions} warm-up  |  "
        f"fills at next open, T+{cfg['settlement_days']}"
    )
    print("-" * 64)
    rows = [
        ("Capital", f"{rupees(cfg['capital_paise']):,.2f}"),
        ("Final equity", f"{rupees(m['final_equity_paise']):,.2f}"),
        ("Final cash", f"{rupees(m['final_cash_paise']):,.2f}"),
        ("Total return %", f"{m['total_return_pct']:.2f}"),
        ("CAGR %", f"{m['cagr_pct']:.2f}"),
        ("Max drawdown %", f"{m['max_drawdown_pct']:.2f}"),
        ("Sharpe", f"{m['sharpe']:.3f}"),
        ("Turnover", f"{m['turnover']:.3f}"),
        ("Avg cash %", f"{m['avg_cash_pct']:.2f}"),
        ("Total fees", f"{rupees(m['total_fees_paise']):,.2f}"),
        ("Fills", f"{m['n_fills']}"),
        ("Buys cut for cash", f"{m['n_released_unfunded']}"),
        ("Orders unfilled at end", f"{m['n_unfilled_at_end']}"),
    ]
    for label, value in rows:
        print(f"{label:<26}{value:>38}")
    print("=" * 64)
    print(f"run_hash {out['run_hash']}")


def main(argv: list[str] | None = None) -> dict[str, Any]:
    return Alpha30BacktestExample().main(argv)


if __name__ == "__main__":
    main()
