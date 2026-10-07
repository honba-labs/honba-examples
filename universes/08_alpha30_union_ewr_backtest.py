"""Example 08: Equal-weight rebalancer over Alpha-30 universe.

Portfolio rebalancing strategy that maintains equal weights across the Alpha-30
basket on a fixed calendar schedule. Runs over NSE daily bars from the Parquet store.

* Cash pool          one shared pool across the basket
* Session calendar   ``day0`` = first session on/after ``--start``. Day 0 buys the
  basket. Then every ``rebalance_days`` calendar days from ``--start`` rebalances.
* Equal weight       ``pool = cash + sum(qty * close)`` over active symbols.
  Unaffordable unheld stocks are excluded from target denominator to avoid idle cash traps.
* Tolerance band     Holdings within ``--tolerance-pct`` (default 5%) of equal weight are
  preserved without trading, avoiding churn from daily micro-drifts.
* Cash sweep         Residual cash after floor division is swept greedily into holdings
  furthest below target, keeping uninvested cash under 1%.
* Order of trades    all sells first (they raise the cash), then buys sorted by
  largest rupee shortfall first.
* Costs              ``fee`` fraction of traded notional per side, deducted from cash.
* Filling            at that session's close.

Run::

    python 08_alpha30_union_ewr_backtest.py
    python 08_alpha30_union_ewr_backtest.py --start 2026-01-01 --end 2026-09-23
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from honba.domain.instrument import InstrumentId

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:  # plain checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba_examples.base import HonbaExample
from honba_examples.ewr import (
    FEE_RATE,
    REBALANCE_DAYS,
    STARTING_CAPITAL,
    TOLERANCE_PCT,
    SimResult,
    buy_shortfall,
    format_changes,
    print_summary,
    rebalance_changes,
    rebalance_orders,
    rebalance_sessions,
    sell_excess,
    simulate,
    trade_changes,
)
from honba_examples.settlement import resolve_settlement_days

# Re-export engine symbols for backwards compatibility with tests and callers
__all__ = [
    "BASKET",
    "FEE_RATE",
    "REBALANCE_DAYS",
    "STARTING_CAPITAL",
    "TOLERANCE_PCT",
    "Alpha30EWRExample",
    "SimResult",
    "buy_shortfall",
    "format_changes",
    "main",
    "print_summary",
    "rebalance_changes",
    "rebalance_orders",
    "rebalance_sessions",
    "sell_excess",
    "simulate",
    "trade_changes",
]

# Canonical universe constituents for Alpha-30 Union on NSE
BASKET: tuple[str, ...] = (
    "ABCAPITAL",
    "ADANIENSOL",
    "ADANIGREEN",
    "ADANIPOWER",
    "ASHOKLEY",
    "AUBANK",
    "AUROPHARMA",
    "BHARATFORG",
    "BHEL",
    "BSE",
    "CUMMINSIND",
    "FEDERALBNK",
    "GVT&D",
    "HINDALCO",
    "IDEA",
    "INDIANB",
    "LAURUSLABS",
    "LTF",
    "MAHABANK",
    "MCX",
    "MOTHERSON",
    "NATIONALUM",
    "NYKAA",
    "PAYTM",
    "POLYCAB",
    "POWERINDIA",
    "SAIL",
    "SHRIRAMFIN",
    "UNIONBANK",
    "VEDL",
)


class Alpha30EWRExample(HonbaExample):
    """Equal-weight rebalance over the Alpha-30 universe."""

    universe_name: str = "nifty200_alpha_30"
    exchange: str = "NSE"
    timeframe: str = "1D"
    start_date: dt.date = dt.date(2026, 1, 1)
    end_date: dt.date = dt.date(2026, 9, 23)
    initial_capital: float = STARTING_CAPITAL
    warmup_days: int = 0
    fee: float = FEE_RATE
    rebalance_days: int = REBALANCE_DAYS
    settlement_days: int | None = None
    tolerance_pct: float = TOLERANCE_PCT
    sweep: bool = True

    def load_universe(self) -> list[InstrumentId]:
        return [InstrumentId(sym, self.exchange) for sym in BASKET]

    def add_custom_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--fee", type=float, default=self.fee, help="Fraction of traded notional, per side"
        )
        parser.add_argument(
            "--rebalance-days",
            type=int,
            default=self.rebalance_days,
            help="Calendar days between rebalances",
        )
        parser.add_argument(
            "--settlement-days",
            type=int,
            default=None,
            help="Sessions between a sell and the buys it funds (default: date-aware cycle)",
        )
        parser.add_argument(
            "--tolerance-pct",
            type=float,
            default=self.tolerance_pct,
            help="Relative drift tolerance band before rebalancing a holding (default: 0.05 = 5%%)",
        )
        parser.add_argument(
            "--no-sweep",
            dest="sweep",
            action="store_false",
            help="Disable greedy residual cash sweep into holdings",
        )

    @staticmethod
    def engine_settlement_days(exchange: str, as_of: dt.date | None = None) -> int:
        """Settlement days for the exchange on ``as_of``, from the core market pack."""
        from honba.markets.india.settlement import settlement_days_for

        return settlement_days_for(exchange, as_of=as_of)

    def resolve_settlement_days(self) -> int:
        """The explicit ``--settlement-days`` if given, else the cycle as of ``start_date``."""
        days, _ = resolve_settlement_days(
            self.exchange,
            as_of=self.start_date,
            cli_value=self.settlement_days,
        )
        return days

    def run(self) -> SimResult:
        universe = self.load_universe()
        symbols = [iid.symbol for iid in universe]
        bars = self.load_bars(universe, self.start_date, self.end_date)
        available = {b.instrument_id.symbol for b in bars}
        missing = [s for s in symbols if s not in available]

        print(
            f"[Bars] {len(bars):,} bars across {len(available)}/{len(symbols)} symbols, "
            f"{self.start_date} → {self.end_date}"
        )
        if missing:
            print(f"[Data] missing bars for {len(missing)}: {', '.join(missing)}")

        settlement_days = self.resolve_settlement_days()
        result = simulate(
            bars,
            symbols,
            start=self.start_date,
            capital=self.initial_capital,
            rebalance_days=self.rebalance_days,
            fee=self.fee,
            settlement_days=settlement_days,
            tolerance_pct=self.tolerance_pct,
            sweep=self.sweep,
        )
        result.missing_data = missing
        print(f"[Model] settlement T+{settlement_days}")
        print_summary(result, self.output)
        return result


def main() -> None:
    Alpha30EWRExample().main()


if __name__ == "__main__":
    main()
