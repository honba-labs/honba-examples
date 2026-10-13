"""screening/01_multibagger_screener.py: Prospective Multibagger Screener (Stage 2 Momentum).

Point-in-time Stage 2 breakout screener evaluated strictly as of ``--asof``. Emits a pure
symbol/JSON stream on ``stdout`` and diagnostics on ``stderr`` for Unix pipe chaining.

Usage:
    # Screen top 5 multibaggers as of 2023-01-01 and pipe the symbols into the forward SIP backtest
    python screening/01_multibagger_screener.py --asof 2023-01-01 --top 5 --format symbols \\
        | python backtesting/08_chained_screener_and_sip.py --asof 2023-01-01 --sip-amount 10000
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba_examples.screener_cli import ScreenerCLIExample, rich_console
from honba_examples.screening import screen_multibaggers

__all__ = ["MultibaggerScreenerExample", "main"]


class MultibaggerScreenerExample(ScreenerCLIExample):
    """Prospective "going-to-be" multibagger screener (Minervini Stage 2 momentum)."""

    train_years: int = 3
    min_return_1y: float = 15.0
    max_return_1y: float = 175.0
    max_pct_from_high: float = 15.0
    max_debt_equity: float = 1.2
    top: int = 10

    def add_custom_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--train-years",
            type=int,
            default=self.train_years,
            help="In-sample training years prior to --asof (default: 3)",
        )
        parser.add_argument(
            "--min-return-1y",
            type=float,
            default=self.min_return_1y,
            help="Minimum 1-year return %% (default: 15.0)",
        )
        parser.add_argument(
            "--max-return-1y",
            type=float,
            default=self.max_return_1y,
            help="Maximum 1-year return %% to avoid parabolic tops (default: 175.0)",
        )
        parser.add_argument(
            "--max-pct-from-high",
            type=float,
            default=self.max_pct_from_high,
            help="Maximum %% below the 52-week high (default: 15.0)",
        )
        parser.add_argument(
            "--max-debt-equity",
            type=float,
            default=self.max_debt_equity,
            help="Maximum debt-to-equity ratio (default: 1.2)",
        )
        parser.add_argument(
            "--top", type=int, default=self.top, help="Limit to top N candidates (default: 10)"
        )

    def screen(self, catalog_dir: Path, asof: dt.date) -> list[dict[str, Any]]:
        return screen_multibaggers(
            catalog_dir=catalog_dir,
            asof_date=asof,
            train_years=self.train_years,
            min_return_1y=self.min_return_1y,
            max_return_1y=self.max_return_1y,
            max_pct_from_high=self.max_pct_from_high,
            max_debt_to_equity=self.max_debt_equity,
            top=self.top,
        )

    def json_payload(self, candidates: list[dict[str, Any]], asof: dt.date) -> dict[str, Any]:
        return {
            "asof_date": asof.isoformat(),
            "strategy": "multibagger",
            "train_years": self.train_years,
            "candidates": candidates,
        }

    def render_table(self, candidates: list[dict[str, Any]], asof: dt.date) -> None:
        from rich import box
        from rich.table import Table

        table = Table(
            title=f"Prospective Multibaggers as of {asof} ({self.train_years}-Yr Base Window)",
            border_style="cyan",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        for col in (
            "Symbol",
            "Price (₹)",
            "1-Yr Ret",
            "3-Mo Surge",
            "From 52w High",
            "Vol Surge",
            "200 SMA",
            "ROCE %",
            "Debt/Eq",
            "MB Score",
        ):
            table.add_column(col, justify="right" if col != "Symbol" else "left")
        for c in candidates:
            roce_s = f"{c['roce_pct']:.1f}%" if c.get("roce_pct") is not None else "-"
            de_s = f"{c['debt_to_equity']:.2f}" if c.get("debt_to_equity") is not None else "-"
            vol_s = f"{c['vol_surge']:.2f}x" if c.get("vol_surge") is not None else "-"
            table.add_row(
                c["symbol"],
                f"₹{c['close']:,.2f}",
                f"{c['ret_1y']:+.1f}%",
                f"{c.get('ret_3m', 0.0):+.1f}%",
                f"{c['pct_from_high']:+.1f}%",
                vol_s,
                f"₹{c['sma200']:,.2f}",
                roce_s,
                de_s,
                f"{c.get('mb_score', 0.0):.1f}",
            )
        rich_console().print(table)


def main(argv: list[str] | None = None) -> Any:
    return MultibaggerScreenerExample().main(argv)


if __name__ == "__main__":
    sys.exit(main())
