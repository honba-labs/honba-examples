"""screening/08_swing_ap_setup_screener.py: Swing-Trading "A+ Setup" Screener.

Point-in-time swing-trading scan evaluated strictly as of ``--asof``, distilled from
startrader.com's "How to Select Stocks for Swing Trading in India". Scans a top-down universe
(default Nifty50) for liquid, moderately volatile names that are either breaking out to a new
20-day high on a volume surge, or pulling back to a rising 20/50-day moving average, reject
gap-downs, score and grade the survivors, and emit a pure symbol/JSON stream on ``stdout``
(diagnostics on ``stderr``) for Unix pipe chaining. The default output (``--min-grade A+``) IS
the watchlist: "every run it provides a list of symbols to watch for an A+ setup."

Usage:
    # Today's A+ watchlist from Nifty50
    python examples/screening/08_swing_ap_setup_screener.py --format table

    # Loosen to grade A or better and pipe symbols into the forward SIP backtest
    python examples/screening/08_swing_ap_setup_screener.py --asof 2024-06-01 --min-grade A --format symbols \\
        | python examples/backtesting/10_chained_screener_and_sip.py --asof 2024-06-01 --sip-amount 10000

Pitfalls::

    - No earnings/corporate-action calendar: the article's "no catalyst in the next 5-10 days"
      check is NOT implemented because this workspace has no earnings-date or corporate-action
      data. A real earnings print (or a stock split, bonus, buyback) can still blow through a
      flagged setup the next session; this screen is blind to it.
    - No spread/pledge/surveillance data: liquidity is approximated by 20-day average daily
      traded value (``--min-adtv``) only, since there is no bid-ask spread series. The article's
      other red-flag checks (promoter pledge %, exchange surveillance/ASM-GSM lists) are not
      implemented either -- a thinly-traded or flagged name can still pass every gate here.
    - ATR is a simple 14-day average of true range, not Wilder-smoothed (the usual charting-
      platform default), so ``atr_pct`` will read slightly different from what a broker terminal
      shows for the same symbol and date.
    - Relative strength is a proxy: ``rs_excess`` compares a symbol's 20-day return to the
      as-of average 20-day return of every OTHER scanned universe member, not to a real Nifty50
      index level (the catalog holds only individual-stock bars, no index series). Swapping
      ``--universe all`` in changes the benchmark and can re-grade every candidate.
    - "A+"/"A"/"B"/"C" are this script's own hand-weighted score bands (``score`` sums five
      capped sub-scores), not an industry-standard definition -- as fragile to a small threshold
      or lookback change as ``01``'s ``mb_score``. Treat the grade as a ranking aid, not ground
      truth, and expect the top-N list to reshuffle with small input changes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

from honba_examples.screener_cli import ScreenerCLIExample
from honba_examples.screening import screen_swing_setups

__all__ = ["SwingApSetupScreenerExample", "main"]


class SwingApSetupScreenerExample(ScreenerCLIExample):
    """Swing-trading "A+ setup" screener (breakout / pullback, RS, volatility, liquidity)."""

    universe: str = "nifty50"
    lookback_days: int = 260
    min_adtv: float = 5_000_000.0
    min_price: float = 50.0
    max_price: float = 0.0
    min_atr_pct: float = 1.0
    max_atr_pct: float = 5.0
    min_rs_excess: float = 0.0
    min_vol_surge: float = 1.5
    pullback_pct: float = 3.0
    max_gap_down_pct: float = 7.0
    max_stale_days: int = 10
    min_grade: str = "A+"
    top: int = 10

    def add_custom_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--universe",
            choices=("nifty50", "all"),
            default=self.universe,
            help="Scan universe: 'nifty50' constituents (default) or 'all' catalog symbols",
        )
        parser.add_argument(
            "--lookback-days",
            type=int,
            default=self.lookback_days,
            help="Calendar days of history to load before --asof (default: 260)",
        )
        parser.add_argument(
            "--min-adtv",
            type=float,
            default=self.min_adtv,
            help="Minimum 20-day average daily traded value, close*volume (default: 5,000,000)",
        )
        parser.add_argument(
            "--min-price",
            type=float,
            default=self.min_price,
            help="Minimum close price (default: 50.0)",
        )
        parser.add_argument(
            "--max-price",
            type=float,
            default=self.max_price,
            help="Maximum close price; 0 disables (default: 0, disabled)",
        )
        parser.add_argument(
            "--min-atr-pct",
            type=float,
            default=self.min_atr_pct,
            help="Minimum ATR%% = atr14 / close * 100 (default: 1.0)",
        )
        parser.add_argument(
            "--max-atr-pct",
            type=float,
            default=self.max_atr_pct,
            help="Maximum ATR%% (default: 5.0)",
        )
        parser.add_argument(
            "--min-rs-excess",
            type=float,
            default=self.min_rs_excess,
            help="Minimum 20-day return %% above the universe's own average (default: 0.0)",
        )
        parser.add_argument(
            "--min-vol-surge",
            type=float,
            default=self.min_vol_surge,
            help="Minimum volume / 20-day baseline volume for a breakout (default: 1.5)",
        )
        parser.add_argument(
            "--pullback-pct",
            type=float,
            default=self.pullback_pct,
            help="Max %% above the 20-day SMA still counted as a pullback (default: 3.0)",
        )
        parser.add_argument(
            "--max-gap-down-pct",
            type=float,
            default=self.max_gap_down_pct,
            help="Reject symbols whose open gapped down more than this %% below prior close (default: 7.0)",
        )
        parser.add_argument(
            "--max-stale-days",
            type=int,
            default=self.max_stale_days,
            help=(
                "Skip symbols whose last bar is more than N calendar days before --asof "
                "(delisted/suspended; default: 10, 0 disables)"
            ),
        )
        parser.add_argument(
            "--min-grade",
            choices=("A+", "A", "B", "C"),
            default=self.min_grade,
            help="Minimum grade to keep after scoring (default: A+)",
        )
        parser.add_argument(
            "--top", type=int, default=self.top, help="Limit to top N candidates (default: 10)"
        )

    def screen(self, catalog_dir: Path, asof: dt.date) -> list[dict[str, Any]]:
        return screen_swing_setups(
            catalog_dir=catalog_dir,
            asof_date=asof,
            universe=self.universe,
            lookback_days=self.lookback_days,
            min_adtv=self.min_adtv,
            min_price=self.min_price,
            max_price=self.max_price,
            min_atr_pct=self.min_atr_pct,
            max_atr_pct=self.max_atr_pct,
            min_rs_excess=self.min_rs_excess,
            min_vol_surge=self.min_vol_surge,
            pullback_pct=self.pullback_pct,
            max_gap_down_pct=self.max_gap_down_pct,
            max_stale_days=self.max_stale_days,
            min_grade=self.min_grade,
            top=self.top,
        )

    def json_payload(self, candidates: list[dict[str, Any]], asof: dt.date) -> dict[str, Any]:
        return {
            "asof_date": asof.isoformat(),
            "strategy": "swing_ap_setup",
            "universe": self.universe,
            "min_grade": self.min_grade,
            "candidates": candidates,
        }

    def render_table(self, candidates: list[dict[str, Any]], asof: dt.date) -> None:
        from honba.display import Column, render_table

        from honba_examples.display_style import (
            SCORE,
            SECONDARY,
            SURGE,
            SYMBOL,
            grade_style_fn,
            pnl_style_fn,
        )

        columns = [
            Column("symbol", "Symbol", style=SYMBOL),
            Column("close", "Close", align="right", fmt=lambda v: f"₹{v:,.2f}"),
            Column("grade", "Grade", align="right", style_fn=grade_style_fn),
            Column("score", "Score", align="right", fmt=lambda v: f"{v:.1f}", style=SCORE),
            Column("setup", "Setup"),
            Column("atr_pct", "ATR%", align="right", fmt=lambda v: f"{v:.2f}%"),
            Column(
                "rs_excess",
                "RS Excess",
                align="right",
                fmt=lambda v: f"{v:+.2f}%",
                style_fn=pnl_style_fn,
            ),
            Column("vol_surge", "Vol Surge", align="right", fmt=lambda v: f"{v:.2f}x", style=SURGE),
            Column("stop_loss", "Stop", align="right", fmt=lambda v: f"₹{v:,.2f}", style=SECONDARY),
            Column(
                "target_1r", "Target 1R", align="right", fmt=lambda v: f"₹{v:,.2f}", style=SECONDARY
            ),
            Column(
                "target_2r", "Target 2R", align="right", fmt=lambda v: f"₹{v:,.2f}", style=SECONDARY
            ),
        ]
        render_table(
            candidates,
            columns,
            title=f"Swing A+ Setups as of {asof} (universe: {self.universe}, min grade: {self.min_grade})",
        )


def main(argv: list[str] | None = None) -> Any:
    return SwingApSetupScreenerExample().main(argv)


if __name__ == "__main__":
    sys.exit(main())
