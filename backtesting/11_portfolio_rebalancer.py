"""backtesting/11_portfolio_rebalancer: forward equal-weight periodic rebalance backtester.

Composable backtest engine for the screening → backtest runtime plane (roadmap §4,
``backtesting/11_portfolio_rebalancer``).  Thin harness over
:mod:`honba_examples.pipeline`: it ingests a symbol stream from ``stdin``
(``--format symbols/json``), ``--symbols`` or its own screener, then periodically
rebalances to **equal weight** with the NSE delivery cost / T+1 settlement model
through :class:`honba_examples.pipeline.execution.SipBacktestHarness`.

For a SIP (rupee-cost averaging) variant use ``09_forward_sip_backtest`` /
``10_chained_screener_and_sip``; this harness is the complementary rebalance leg
(``B2`` in the roadmap's plane-A diagram).

Usage::

    # Equal-weight rebalance on multibaggers screened as of 2023-01-01
    python screening/01_multibagger_screener.py --asof 2023-01-01 --top 5 --format symbols \\
      | python backtesting/11_portfolio_rebalancer.py --asof 2023-01-01 --capital 100000

    # With explicit symbols
    python backtesting/11_portfolio_rebalancer.py --symbols RELIANCE,TCS,INFY --asof 2023-01-01

    # JSON stream in, table out
    python screening/03_dividend_yield_screener.py --asof 2025-03-01 --top 3 --format json \\
      | python backtesting/11_portfolio_rebalancer.py --asof 2025-03-01 --capital 50000 --format json

For the in-code DSL see ``strategies/11_contrarian_value.py`` (and friends) which compose
``MultibaggerScreener >> EqualWeightSizer >> SipBacktestHarness`` directly.

.. versionadded:: 0.52
   Roadmap Phase 4 (``11_portfolio_rebalancer``).

Pitfalls::

    - Docstring versus code: the summary promises periodic rebalancing with the NSE delivery cost
      and T+1 settlement model, but SipBacktestHarness wraps simulate_portfolio_sip, which only buys
      and holds (monthly SIP at each month's first open). There is no rebalance, no cost pack, no
      settlement, and fractional shares. Equal weight is only the split of each contribution;
      weights drift with prices.
    - Symbol source: the --symbols example (RELIANCE,TCS,INFY) is a list picked today with
      hindsight. Only a list produced by a screen run as of the same --asof is a fair forward test.
    - Same-day boundary: the harness buys at the open of the first session on or after --asof, while
      the screeners use that day's close, so a trading-day --asof leaks one session (see
      10_chained_screener_and_sip).
    - Name traps: --strategy all maps to ValueScreener (it does not run every screener), and
      --allocation is a no-op here because EqualWeightSizer always puts 1/N weights on the context
      and the harness prefers context weights. A flag that changes nothing is easy to mistake for a
      comparison you ran.
    - One window, one regime: a single --asof to the latest session with a handful of names is one
      sample. Vary --asof and compare against an index before concluding anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba.display import OutputFormat
from rich import box
from rich.console import Console
from rich.table import Table

from honba_examples.cli import add_out_arg
from honba_examples.pipeline import EqualWeightSizer, SipBacktestHarness
from honba_examples.pipeline.base import Pipeline
from honba_examples.pipeline.io import read_symbols
from honba_examples.screener_cli import ScreenerCLIExample  # noqa: F401

__all__ = ["PortfolioRebalancerExample", "main"]

console = Console()
err_console = Console(stderr=True)


class PortfolioRebalancerExample:
    """Equal-weight periodic rebalancer — stdin-aware composition over the pipeline."""

    top: int = 5
    strategy: str = "multibagger"
    asof_str: str | None = None
    end_date_str: str | None = None
    capital: float = 100000.0
    sip_amount: float | None = None
    allocation: str = "equal"
    symbols: str | None = None
    output_format: OutputFormat = OutputFormat.TABLE

    def add_custom_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--strategy",
            choices=["multibagger", "value", "dividend", "all"],
            default=self.strategy,
            help="screener when symbols are not supplied (default: multibagger)",
        )
        parser.add_argument(
            "--asof", dest="asof_str", type=str, default=None, help="screening date (YYYY-MM-DD)"
        )
        parser.add_argument(
            "--end-date",
            dest="end_date_str",
            type=str,
            default=None,
            help="backtest end (YYYY-MM-DD, default: latest catalog session)",
        )
        parser.add_argument(
            "--capital",
            type=float,
            default=self.capital,
            help="initial capital deployed on --asof (INR)",
        )
        parser.add_argument(
            "--sip-amount",
            type=float,
            default=None,
            help="monthly SIP per portfolio (INR; optional)",
        )
        parser.add_argument(
            "--allocation",
            choices=["equal", "52w-ratio", "score"],
            default=self.allocation,
            help="weight model (default: equal)",
        )
        parser.add_argument(
            "--top", type=int, default=self.top, help="top-N when screening (default: 5)"
        )
        parser.add_argument(
            "--symbols", type=str, default=None, help="comma-separated symbols or - for stdin"
        )
        parser.add_argument(
            "--format",
            dest="output_format",
            type=lambda s: OutputFormat.parse(s),
            choices=[OutputFormat.TABLE, OutputFormat.PLAIN, OutputFormat.JSON],
            default=self.output_format,
            help="output format (table, plain, json)",
        )

    def _parse_argv(self, argv: list[str] | None) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            description=(__doc__ or "Equal-weight rebalancer").split("\n\nPitfalls::")[0] + "\n"
        )
        self.add_custom_args(parser)
        parser.add_argument("--data-dir", type=Path, default=None)
        add_out_arg(parser)
        parsed = parser.parse_args(argv)
        for key, value in vars(parsed).items():
            if hasattr(self, key):
                setattr(self, key, value)
        # keep the extra parsed attrs for --out/--data-dir
        self._extra_data_dir: Path | None = getattr(parsed, "data_dir", None)
        self._extra_out: Path | None = getattr(parsed, "out", None)
        return parsed

    def run(self, argv: list[str] | None = None) -> int:
        # Resolve symbols via the same pipeline.io contract used by 08.
        syms: list[str] | None = None
        if self.symbols is not None and self.symbols.strip() not in ("", "-"):
            syms = [s.strip().upper() for s in self.symbols.split(",") if s.strip()]
        else:
            piped = read_symbols()
            if piped:
                syms = piped
            elif self.symbols is not None and self.symbols.strip() == "-":
                err_console.print("[red]Error:[/red] --symbols - but no symbols on stdin")
                return 1

        # If we have piped/explicit symbols, honour --top; otherwise drive the screener path
        # through SipBacktestHarness after pre-populating the context.
        # Easiest: delegate symbol resolution to the 08 harness by constructing a minimal
        # pipeline that either already carries symbols or carries a screener step.
        from honba_examples.pipeline.screeners import (
            DividendYieldScreener,
            MultibaggerScreener,
            ValueScreener,
        )

        if syms is not None:
            syms = syms[: self.top] if self.top and self.top > 0 else syms
            pipeline = Pipeline([EqualWeightSizer()])
            initial_ctx: dict[str, Any] = {
                "symbols": syms,
                "candidates": [{"symbol": s} for s in syms],
            }
        else:
            table = {
                "multibagger": MultibaggerScreener,
                "value": ValueScreener,
                "dividend": DividendYieldScreener,
                "all": ValueScreener,
            }
            screened: Any
            if self.strategy == "multibagger":
                screened = table["multibagger"](asof=self.asof_str, top=self.top)
            elif self.strategy == "dividend":
                screened = DividendYieldScreener(asof=self.asof_str, top=self.top)
            else:
                screened = ValueScreener(asof=self.asof_str, top=self.top)
            pipeline = screened >> EqualWeightSizer()
            initial_ctx = {}

        # Resolve the actual asof for the harness: prefer the one carried on the context after
        # screening, else --asof, else the catalog horizon.
        # Build the harness last so it sees the symbols/weights placed before it.
        asof_value = self.asof_str
        end_value = self.end_date_str
        harness = SipBacktestHarness(
            asof=asof_value,
            end_date=end_value,
            initial_capital=float(self.capital) if self.capital is not None else None,
            monthly_sip=float(self.sip_amount) if self.sip_amount is not None else None,
            allocation=self.allocation,
        )
        pipeline = pipeline >> harness

        result = pipeline.run(initial_ctx)
        if not result.get("symbols"):
            err_console.print("[yellow]No candidates for the requested screener / asof.[/yellow]")
            return 0

        fmt = self.output_format
        if fmt is OutputFormat.JSON or str(fmt).lower() == "json":
            payload: dict[str, Any] = {
                "strategy": self.strategy,
                "symbols": result.get("symbols"),
                "candidates": result.get("candidates"),
                "weights": result.get("weights"),
                "summary": result.get("summary"),
                "stocks": result.get("stocks"),
            }
            print(json.dumps(payload, indent=2, default=str))
            if self._extra_out:
                self._extra_out.parent.mkdir(parents=True, exist_ok=True)
                self._extra_out.write_text(json.dumps(payload, indent=2, default=str) + "\n")
            return 0
        if fmt is OutputFormat.PLAIN:
            print(" ".join(str(s) for s in (result.get("symbols") or [])))
            return 0

        # Table
        summary: dict[str, Any] = result.get("summary") or {}
        stocks: list[dict[str, Any]] = result.get("stocks") or []
        table = Table(
            title=f"Equal-Weight Rebalance — {len(stocks)} symbols (asof {result.get('asof', self.asof_str)})",
            box=box.ROUNDED,
            header_style="bold cyan",
            border_style="cyan",
        )
        table.add_column("Symbol", style="bold cyan")
        table.add_column("Weight %", justify="right")
        table.add_column("Invested", justify="right")
        table.add_column("Final Value", justify="right", style="bold green")
        table.add_column("Profit", justify="right")
        table.add_column("XIRR %", justify="right", style="magenta")
        for s in stocks:
            xirr = s.get("xirr_pct")
            table.add_row(
                str(s.get("symbol", "")),
                f"{float(s.get('weight_pct', 0.0)):.1f}",
                f"{float(s.get('total_invested', 0.0)):,.0f}",
                f"{float(s.get('final_val', 0.0)):,.0f}",
                f"{float(s.get('profit', 0.0)):+,.0f}",
                f"{float(xirr):+.1f}" if xirr is not None else "N/A",
            )
        console.print(table)
        if summary:
            xirr = summary.get("portfolio_xirr")
            xirr_str = f"{float(xirr):+.1f}%" if xirr is not None else "N/A"
            console.print(
                f"Invested ₹{float(summary.get('total_invested', 0.0)):,.0f} | "
                f"Value ₹{float(summary.get('total_value', 0.0)):,.0f} | "
                f"Profit ₹{float(summary.get('total_profit', 0.0)):+,.0f} | "
                f"Multiple {float(summary.get('portfolio_multiple', 1.0)):.2f}x | "
                f"XIRR {xirr_str}"
            )
        if self._extra_out:
            payload = {
                "strategy": self.strategy,
                "symbols": result.get("symbols"),
                "summary": summary,
                "stocks": stocks,
            }
            self._extra_out.parent.mkdir(parents=True, exist_ok=True)
            self._extra_out.write_text(json.dumps(payload, indent=2, default=str) + "\n")
        return 0

    def main(self, argv: list[str] | None = None) -> int:
        if argv is None:
            argv = sys.argv[1:]
        if not hasattr(self, "_extra_out"):
            self._extra_out = None
        if not hasattr(self, "_extra_data_dir"):
            self._extra_data_dir = None
        self._parse_argv(argv)
        return self.run(argv)


def main(argv: list[str] | None = None) -> int:
    return PortfolioRebalancerExample().main(argv)


if __name__ == "__main__":
    sys.exit(main())
