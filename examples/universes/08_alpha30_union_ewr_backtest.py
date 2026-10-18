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

    python universes/08_alpha30_union_ewr_backtest.py
    python universes/08_alpha30_union_ewr_backtest.py --start 2026-01-01 --end 2026-09-23
    python universes/08_alpha30_union_ewr_backtest.py --initial-corpus 1000000 --sip 50000 --no-of-sip 12
    python universes/08_alpha30_union_ewr_backtest.py --initial-corpus 500000 --sip 25000 --duration 180d

Pitfalls::

    - Observe and trade the same close: the pool is valued at a session's close and the
      rebalance fills at that same close. That is the "observe and trade the close" bias, so
      results look better than the next-open fills of 07. Use 07 for a Honba-native number.
    - Which Alpha 30 list: by default the basket is the engine list (the one 07 trades), so 07 and
      08 are comparable; --alpha30-list seed switches to the older seed list (four names differ).
      Names with no bars are dropped with a [Data] message and the others are rebalanced as if
      they were the whole basket; today's members are also survivors of any earlier window.
    - Tunable knobs: --tolerance-pct, --rebalance-days, --fee and the cash sweep all change the
      result on a window of about nine months. Sweeping them and keeping the best is overfitting.
    - Leverage and shorting options track margin use, but a margin call is only counted (no forced
      liquidation) and no interest or borrow cost is charged (the printed summary and
      SimResult.notes say so); the flat --fee is not the statutory cost stack of 02_cost_modeling.
    - The schedule is anchored to --start in calendar days, so the result depends on the start
      date; vary it before drawing conclusions.
"""

from __future__ import annotations

import argparse
import datetime as dt

from honba.domain.instrument import InstrumentId

from honba_examples.base import HonbaExample
from honba_examples.ewr import (
    DEFAULT_INITIAL_MARGIN,
    DEFAULT_LEVERAGE,
    DEFAULT_MAINTENANCE_MARGIN,
    FEE_RATE,
    REBALANCE_DAYS,
    STARTING_CAPITAL,
    TOLERANCE_PCT,
    SimResult,
    buy_shortfall,
    format_changes,
    parse_duration_to_days,
    print_summary,
    rebalance_changes,
    rebalance_orders,
    rebalance_sessions,
    sell_excess,
    simulate,
    trade_changes,
)
from honba_examples.settlement import resolve_settlement_days
from honba_examples.universe_lists import (
    ALPHA30_ENGINE,
    ALPHA30_VARIANTS,
    DEFAULT_ALPHA30_VARIANT,
    alpha30_instruments,
)

# Re-export engine symbols for backwards compatibility with tests and callers
__all__ = [
    "BASKET",
    "DEFAULT_INITIAL_MARGIN",
    "DEFAULT_LEVERAGE",
    "DEFAULT_MAINTENANCE_MARGIN",
    "FEE_RATE",
    "REBALANCE_DAYS",
    "STARTING_CAPITAL",
    "TOLERANCE_PCT",
    "Alpha30EWRExample",
    "SimResult",
    "buy_shortfall",
    "format_changes",
    "main",
    "parse_duration_to_days",
    "print_summary",
    "rebalance_changes",
    "rebalance_orders",
    "rebalance_sessions",
    "sell_excess",
    "simulate",
    "trade_changes",
]

# Default basket = the engine list (what 07 trades); --alpha30-list seed selects the older seed.
BASKET: tuple[str, ...] = ALPHA30_ENGINE


class Alpha30EWRExample(HonbaExample):
    """Equal-weight rebalance over the Alpha-30 universe."""

    universe_name: str = "nifty200_alpha_30"
    exchange: str = "NSE"
    timeframe: str = "1D"
    start_date: dt.date = dt.date(2026, 1, 1)
    end_date: dt.date = dt.date(2026, 9, 23)
    initial_capital: float | None = STARTING_CAPITAL
    warmup_days: int = 0
    fee: float = FEE_RATE
    rebalance_days: int = REBALANCE_DAYS
    settlement_days: int | None = None
    tolerance_pct: float = TOLERANCE_PCT
    sweep: bool = True
    leverage: float = 1.0
    initial_margin: float = DEFAULT_INITIAL_MARGIN
    maintenance_margin: float = DEFAULT_MAINTENANCE_MARGIN
    allow_short: bool = False
    initial_corpus: float | None = None
    sip: float = 0.0
    duration: str | None = None
    no_of_sip: int | None = None
    alpha30_list: str = DEFAULT_ALPHA30_VARIANT

    def load_universe(self) -> list[InstrumentId]:
        return alpha30_instruments(self.alpha30_list, self.exchange)

    def add_custom_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--alpha30-list",
            choices=ALPHA30_VARIANTS,
            default=self.alpha30_list,
            help="engine = the list resolve_universe returns and 07 trades (default); "
            "seed = older alternative snapshot",
        )
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
        parser.add_argument(
            "--leverage",
            type=float,
            default=self.leverage,
            help="Portfolio leverage multiplier (default: 1.0, e.g. 2.0 for margin)",
        )
        parser.add_argument(
            "--initial-margin",
            type=float,
            default=self.initial_margin,
            help=f"Initial margin requirement fraction (default: {DEFAULT_INITIAL_MARGIN})",
        )
        parser.add_argument(
            "--maintenance-margin",
            type=float,
            default=self.maintenance_margin,
            help=f"Maintenance margin requirement fraction (default: {DEFAULT_MAINTENANCE_MARGIN})",
        )
        parser.add_argument(
            "--allow-short",
            action="store_true",
            default=self.allow_short,
            help="Allow short selling positions",
        )
        parser.add_argument(
            "--initial-corpus",
            type=float,
            default=None,
            help="Starting initial investment corpus in INR (overrides --initial-capital / --capital)",
        )
        parser.add_argument(
            "--sip",
            "--sip-amount",
            dest="sip",
            type=float,
            default=self.sip,
            help="SIP installment amount in INR injected every rebalance session",
        )
        parser.add_argument(
            "--duration",
            "--sip-duration",
            dest="duration",
            type=str,
            default=self.duration,
            help="Duration for SIP injections (e.g. '180d', '6m', '1y', '180' days, or end date YYYY-MM-DD)",
        )
        parser.add_argument(
            "--no-of-sip",
            "--sip-count",
            dest="no_of_sip",
            type=int,
            default=self.no_of_sip,
            help="Maximum number of SIP installments to execute",
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
        corpus = float(
            self.initial_corpus
            if self.initial_corpus is not None
            else (self.initial_capital if self.initial_capital is not None else STARTING_CAPITAL)
        )
        sip_duration_days = parse_duration_to_days(self.duration, start_date=self.start_date)

        result = simulate(
            bars,
            symbols,
            start=self.start_date,
            capital=corpus,
            rebalance_days=self.rebalance_days,
            sip_amount=self.sip,
            no_of_sip=self.no_of_sip,
            sip_duration_days=sip_duration_days,
            fee=self.fee,
            settlement_days=settlement_days,
            tolerance_pct=self.tolerance_pct,
            sweep=self.sweep,
            leverage=self.leverage,
            initial_margin=self.initial_margin,
            maintenance_margin=self.maintenance_margin,
            allow_short=self.allow_short,
        )
        result.missing_data = missing
        lev_str = f", leverage {self.leverage}x" if self.leverage > 1.0 else ""
        sip_str = f", SIP ₹{self.sip:,.0f}" if self.sip > 0 else ""
        print(f"[Model] settlement T+{settlement_days}{lev_str}{sip_str}")
        print_summary(result, self.output)
        return result


def main() -> None:
    Alpha30EWRExample().main()


if __name__ == "__main__":
    main()
