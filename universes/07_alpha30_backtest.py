"""Example 07: Honba-native backtest of the Alpha-30 equal-weight catalog strategy.

This is the reference way to backtest a catalog strategy over a universe in Honba.

Pipeline
--------
1. **Strategy** - ``alpha30_equal_weight`` is loaded from the ``honba-strategies``
   catalog by registry name with its ``config.toml``.
2. **Universe** - resolved once, from the universe the strategy trades
   (``nifty200_alpha_30``). The same list is loaded from the Parquet store; members
   with no bars in the test window are reported (``--require-full-coverage`` makes
   that fatal).
3. **Run** - the core ``StrategyRunner`` and ``LedgerContext`` drive the strategy.
   Orders fill at the next session's open with NSE delivery costs and date-aware settlement.
4. **Warm-up** - bars before ``--test-start`` are fed to the strategy for indicators.
5. **Output** - money is integer minor units (paise). ``--out-dir/run.json`` holds the
   complete structured artifact with hashes of inputs and results.

Run::

    python universes/07_alpha30_backtest.py --test-start 2026-06-01 --test-end 2026-09-20 \\
        --out-dir /tmp/alpha30
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

from honba.display import Column, render_kv, render_table
from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.domain.money import Currency, Money
from honba.markets.india.universes import resolve_universe
from honba.strategies.loader import CatalogError

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:  # plain checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba_examples.artifact import write_run_json
from honba_examples.backtest import BacktestRun, run_portfolio_backtest
from honba_examples.base import HonbaExample, ts_to_date
from honba_examples.catalog import load_named
from honba_examples.settlement import resolve_settlement_days

SCHEMA = "honba-examples/07-alpha30-backtest/v1"
DEFAULT_STRATEGY = "alpha30_equal_weight"


def _date(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


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
    """Honba-native Alpha-30 equal-weight backtest."""

    universe_name: str = "nifty200_alpha_30"
    initial_capital: float | None = None
    warmup_days: int = 0
    out_dir: Path = Path("output") / "07_alpha30"
    date_range_args = False

    test_start: dt.date = dt.date(2026, 1, 1)
    test_end: dt.date | None = None
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
            help="Sessions until sale proceeds are spendable",
        )
        parser.add_argument(
            "--require-full-coverage",
            action="store_true",
            help="Fail if any universe member has no bars in the test window",
        )

    def resolve(self, universe_key: str | None) -> list[InstrumentId]:
        universe = resolve_universe(self.universe_name, exchange=self.exchange)
        if universe_key:
            strategy_universe = resolve_universe(universe_key, exchange=self.exchange)
            if set(strategy_universe) != set(universe):
                raise SystemExit(
                    f"--universe {self.universe_name!r} differs from the universe the strategy "
                    f"trades ({universe_key!r}); the loader and the strategy must agree"
                )
        return sorted(set(universe), key=lambda i: (i.symbol, i.exchange))

    def load(self, universe: list[InstrumentId], start: dt.date, end: dt.date) -> list[Bar]:
        return self.load_bars(universe, start, end)


    def run(self) -> dict[str, Any]:
        try:
            loaded = load_named(
                self.strategy,
                strategies_dir=self.strategies_dir,
                search_from=Path(__file__),
            )
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
        capital_minor = Money.from_major(capital, Currency.INR).amount

        settlement, settlement_source = resolve_settlement_days(
            self.exchange,
            as_of=self.test_start,
            cli_value=self.settlement_days,
            cfg_value=cfg.settlement_days,
        )

        warmup_start = self.test_start - dt.timedelta(days=self.warmup_days)
        if self.test_end is not None:
            test_end, end_source = self.test_end, "cli"
            bars = self.load(universe, warmup_start, test_end)
        else:
            today = dt.datetime.now(dt.timezone.utc).date()
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

        strategy = loaded.instantiate()
        result = run_portfolio_backtest(
            strategy,
            bars,
            test_start=self.test_start,
            test_end=test_end,
            capital_minor=capital_minor,
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
            "capital_minor": capital_minor,
            "settlement_days": settlement,
            "settlement_source": settlement_source,
            "fill_model": "next_session_open",
            "cost_model": "nse_equity_delivery, per-leg rounded to minor units",
        }

        out = write_run_json(
            schema=SCHEMA,
            config=config,
            universe=universe,
            coverage=coverage,
            run_result=result,
            bars=bars,
            out_dir=self.ensure_out_dir(),
            paths={"data_dir": str(self.store.data_dir), "strategy_dir": str(loaded.path)},
        )
        print_summary(out, result)
        print(f"[Out] {self.out_dir / 'run.json'}")
        return out


def print_summary(out: dict[str, Any], result: BacktestRun) -> None:
    cfg, m = out["config"], out["result"]["metrics"]

    def rupees(minor: int) -> float:
        return Money.from_minor(minor, Currency.INR).to_major()

    render_kv(
        [
            ("Period", f"{cfg['test_start']} -> {cfg['test_end']}  ({cfg['exchange']})"),
            (
                "Sessions",
                (
                    f"{result.test_sessions} test, {result.warmup_sessions} warm-up  |  "
                    f"fills at next open, T+{cfg['settlement_days']}"
                ),
            ),
        ],
        title="Alpha-30 equal weight",
    )
    rows = [
        ("Capital", f"{rupees(cfg['capital_minor']):,.2f}"),
        ("Final equity", f"{rupees(m['final_equity_minor']):,.2f}"),
        ("Total return %", f"{m['total_return_pct']:+.2f}%"),
        ("CAGR %", f"{m['cagr_pct']:+.2f}%"),
        ("Max drawdown %", f"{m['max_drawdown_pct']:.2f}%"),
        ("Sharpe", f"{m['sharpe']:.3f}"),
        ("Total fees", f"{rupees(m['total_fees_minor']):,.2f}"),
        ("Turnover", f"{m['turnover']:.3f}"),
        ("Fills", f"{m['n_fills']}"),
        ("Buys cut for cash", f"{m['n_released_unfunded']}"),
        ("Orders unfilled at end", f"{m['n_unfilled_at_end']}"),

    ]
    render_table(
        rows,
        [Column("metric", "Metric"), Column("value", "Value", align="right")],
    )
    print(f"run_hash {out['run_hash']}")


def main(args: list[str] | None = None) -> Any:
    return Alpha30BacktestExample().main(args)


if __name__ == "__main__":
    main()
