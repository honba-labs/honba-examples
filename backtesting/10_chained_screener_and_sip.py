"""backtesting/10_chained_screener_and_sip.py: Chained Screener and Forward SIP Backtest.

Demonstrates point-in-time prospective multibagger and Benjamin Graham value screening with in-sample historical data
and subsequent out-of-sample forward backtesting with systematic capital accumulation:
    [Stage 1: Prospective Screener (2-5 Years In-Sample Base & Sweet-Spot Momentum / Value)]
                                      │
                               (top candidates)
                                      ▼
    [Stage 2: Out-of-Sample Forward Backtest for Confirmation]
       (Initial Capital on Asof Date + Recurring Monthly SIP Until Till Date)

Theoretical Foundations:
    1. Multibaggers (2x, 3x, 5x, 10x+) compound via the "Twin Engines" (Phelps & Mayer):
        Return Multiple = (EPS Expansion) × (P/E Multiple Re-rating)
       - Minervini Stage 2 Trend Template: Price >= SMA50 >= SMA200, rising 200 SMA.
       - Consolidation Base & Anti-Exhaustion Guard (near 52w high, capped 1y return).
       - Momentum Acceleration & Institutional Volume Footprints.
       - Capital Efficiency (ROCE >= 12%) and conservative Debt-to-Equity (<= 1.2).

    2. Benjamin Graham Value (*The Intelligent Investor*):
        - Graham Number = sqrt(22.5 * EPS * BVPS)
        - Margin of Safety = (Graham Value - Price) / Graham Value * 100
        - Valuation Discipline: P/E <= 15-20, P/B <= 1.5-2.0, P/E × P/B <= 22.5
        - Solvency: Low Debt-to-Equity, robust Current Ratio, positive audited earnings.

Systematic Execution Model (Chained Screener + SIP):
    - Initial capital deployed across screened candidates on the evaluation date.
    - Allocation model: '52w-ratio' (proximity to 52w high), 'equal', or 'score'.
    - Recurring monthly SIP deployed on the first trading day of each month until terminal date.
    - Calculates terminal valuation, net profit, SIP multiple, lump-sum multiple, peak multiple,
      and exact annualized XIRR.

Usage Examples::

    # 1. Screen prospective multibaggers as of 2024-01-01 and backtest forward SIP to present:
    python backtesting/10_chained_screener_and_sip.py --strategy multibagger --train-years 3 --asof 2024-01-01 --top 3 --capital 100000 --sip 10000

    # 2. Screen Benjamin Graham Defensive value candidates and run forward SIP:
    python backtesting/10_chained_screener_and_sip.py --strategy graham --graham-mode defensive --asof 2024-01-01 --top 3 --capital 100000 --sip 10000

    # 3. Equal allocation model benchmark:
    python backtesting/10_chained_screener_and_sip.py --strategy multibagger --asof 2024-01-01 --top 3 --capital 100000 --sip 10000 --allocation equal

    # 4. Prospective screening as of today (live forward monitoring):
    python backtesting/10_chained_screener_and_sip.py --strategy graham --screen-only --top 5

Pitfalls::

    - Boundary lookahead: the screeners read bars up to and including --asof (bar_date <= asof, so
      that day's close), but simulate_portfolio_sip buys the lump sum at the open of the first bar
      on or after --asof. On a trading day that is the open of a session whose close the screen
      already used. A non-trading --asof (the default 2025-03-01 is a Saturday) avoids it by
      accident.
    - Fundamentals dating: ROCE and debt/equity are chosen with fiscal_date <= asof, the period end,
      not the date the results were published. Unless the store holds filing dates, the screen can
      use numbers the market did not have yet, which flatters quality screens.
    - Survivorship: the screener ranks every symbol under the catalog directory, whatever is on disk
      today, and --universe is not passed to it. In the simulation a symbol with no bars after
      --asof is valued flat at its last close (sip.py) and one with no data is skipped, so a name
      that collapsed or delisted shows 0 percent or vanishes instead of showing a loss.
    - Friction: the forward SIP buys fractional shares at the month's first open with no brokerage,
      STT, stamp duty, slippage or liquidity check, and never rebalances or sells. Net returns are
      overstated, most for small caps.
    - One draw: a single --asof, three to five names and one market regime. The 2x/3x 'Multibagger'
      label uses the forward peak high, a number from the future that suits illustration, not a
      signal. Repeat over many --asof dates and compare with a plain index SIP.
    - Defence shown: the screen reads only data up to --asof, --screen-only prints candidates with
      no forward peek, and an --asof at the end of the data yields no forward window (the horizon
      notice) instead of a fake result.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Ensure honba-examples root is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba.display import OutputFormat
from rich.console import Console

from honba_examples.artifact import write_json_report
from honba_examples.base import HonbaExample
from honba_examples.pipeline.io import parse_symbols, read_symbols
from honba_examples.screening import (
    find_catalog_path,
    get_latest_market_date,
)
from honba_examples.screening.cli_args import add_chain_args
from honba_examples.screening.dispatch import run_screener, symbol_stub
from honba_examples.screening.paths import resolve_eval_window
from honba_examples.screening.render import (
    render_backtest_table,
    render_horizon_notice,
    render_screener_table,
)
from honba_examples.sip import (
    SipPortfolioStrategy,
    allocation_weights,
    simulate_portfolio_sip,
)

__all__ = ["ChainedScreenerSipExample", "SipPortfolioStrategy", "main"]

logger = logging.getLogger(__name__)
console = Console()
err_console = Console(stderr=True)


class ChainedScreenerSipExample(HonbaExample):
    """Chained In-Sample Screener and Out-of-Sample Forward SIP Backtest.

    Inherits from HonbaExample to provide standardized CLI configuration:
    - `--capital` / `--initial-capital`: Initial capital deployed on asof date
    - `--universe`, `--exchange`, `--timeframe`
    - `--start` / `--asof`: In-sample screening evaluation date
    - `--end` / `--end-date`: Out-of-sample forward backtest terminal date
    - `--data-dir`, `--out-dir`, `--format`
    """

    universe_name: str = "nifty50"
    exchange: str = "NSE"
    timeframe: str = "1D"
    start_date: dt.date = dt.date(2023, 1, 1)
    end_date: dt.date = dt.date(2026, 12, 31)
    initial_capital: float | None = None
    warmup_days: int = 0
    date_range_args: bool = True

    # Custom attributes defined as class attributes so HonbaExample.parse_args sets them
    strategy: str = "multibagger"
    graham_mode: str = "defensive"
    asof_str: str | None = None
    end_date_str: str | None = None
    train_years: int = 3
    sip_amount: float | None = None
    sip_mode: str = "portfolio"
    allocation: str = "52w-ratio"
    top: int = 5
    max_stale_days: int = 10
    screen_only: bool = False
    symbols: str | None = None
    out: Path | None = None

    def add_custom_args(self, parser: argparse.ArgumentParser) -> None:
        """Add screener and SIP specific options alongside HonbaExample base args."""
        add_chain_args(
            parser,
            strategy=self.strategy,
            graham_mode=self.graham_mode,
            train_years=self.train_years,
            sip_mode=self.sip_mode,
            allocation=self.allocation,
            top=self.top,
        )

    def run(self) -> Any:
        """Execute Stage 1 screening and Stage 2 forward backtesting."""
        # 1. Resolve catalog path
        try:
            if self.data_dir:
                catalog_dir = find_catalog_path(self.data_dir)
            else:
                candidate = self.store.data_dir / "catalog" / self.timeframe / self.exchange
                catalog_dir = candidate if candidate.is_dir() else find_catalog_path()
        except FileNotFoundError as exc:
            err_console.print(f"[red]Error:[/red] {exc}")
            return 1

        latest_data_date = get_latest_market_date(catalog_dir)

        # 2. Resolve in-sample date (asof) and terminal backtest date
        try:
            asof_eval_date, end_eval_date = resolve_eval_window(
                self.asof_str,
                self.start_date,
                self.end_date_str,
                self.end_date,
                latest_data_date,
                notify=lambda msg: err_console.print(f"[dim]{msg}[/dim]"),
            )
        except ValueError as exc:
            err_console.print(f"[red]Error:[/red] {exc}")
            return 1

        init_capital_val = float(self.initial_capital) if self.initial_capital is not None else None

        # 3. Resolve target symbols: explicit --symbols > stdin pipe > screener
        candidates: list[dict[str, Any]] = []
        target_symbols: list[str] = []
        screened = False

        if self.symbols is not None and self.symbols.strip() not in ("", "-"):
            # Explicit comma-separated list
            target_symbols = [s.strip().upper() for s in self.symbols.split(",") if s.strip()]
            err_console.print(
                f"[dim]Backtesting {len(target_symbols)} explicitly-provided symbols "
                f"({asof_eval_date} to {end_eval_date}).[/dim]"
            )
        else:
            stdin_requested = self.symbols is not None and self.symbols.strip() == "-"
            # Auto-detect a piped stream only when stdin is not an interactive terminal.
            target_symbols = self._read_symbols_from_stdin()
            if not target_symbols:
                if stdin_requested:
                    err_console.print(
                        "[red]Error:[/red] --symbols - requested but no symbols were received on stdin."
                    )
                    return 1
            else:
                err_console.print(
                    f"[dim]Ingested {len(target_symbols)} symbols from stdin "
                    f"({asof_eval_date} to {end_eval_date}).[/dim]"
                )

        if not target_symbols and not screened:
            candidates = run_screener(
                self.strategy,
                catalog_dir,
                asof_eval_date,
                self.train_years,
                self.top,
                self.graham_mode,
                max_stale_days=self.max_stale_days,
            )
            target_symbols = [c["symbol"] for c in candidates]
            screened = True
            err_console.print(
                f"[dim]Screened {len(target_symbols)} candidates as of {asof_eval_date}.[/dim]"
            )

        # Slice piped/explicit symbol lists to --top (screening already applied it)
        if not screened and self.top and self.top > 0:
            target_symbols = target_symbols[: self.top]

        if not screened:
            candidates = [symbol_stub(s, asof_eval_date) for s in target_symbols]

        # Handle plain symbols output
        if self.output_format == OutputFormat.PLAIN:
            print(" ".join(target_symbols))
            return 0

        if not target_symbols:
            err_console.print(
                f"[yellow]No candidates found for strategy '{self.strategy}' as of {asof_eval_date}.[/yellow]"
            )
            return 0

        # Display Stage 1 screener results only when we actually screened (if not JSON)
        if screened and self.output_format != OutputFormat.JSON:
            render_screener_table(
                candidates,
                asof_eval_date,
                self.strategy,
                self.train_years,
                self.graham_mode,
                console,
            )

        # Check if out-of-sample forward backtest can be performed
        has_forward_window = asof_eval_date < end_eval_date

        # If --screen-only requested or no future forward window exists
        if self.screen_only or not has_forward_window:
            if self.output_format == OutputFormat.JSON:
                print(json.dumps({"stage": "screening", "candidates": candidates}, indent=2))
                return 0

            if not self.screen_only and not has_forward_window:
                render_horizon_notice(
                    len(candidates),
                    asof_eval_date,
                    latest_data_date,
                    self.strategy,
                    self.top,
                    console,
                )
            return 0

        # Compute portfolio allocation weights
        weights = allocation_weights(self.allocation, candidates)

        # 4. Stage 2: Out-of-Sample Forward Backtest (initial capital on asof + monthly SIP to end_date)
        portfolio_results = simulate_portfolio_sip(
            catalog_dir=catalog_dir,
            symbols=target_symbols,
            start_date=asof_eval_date,
            end_date=end_eval_date,
            initial_capital=init_capital_val,
            monthly_sip=self.sip_amount,
            sip_mode=self.sip_mode,
            weights=weights,
        )

        combined_output = {
            "asof_date": asof_eval_date.isoformat(),
            "train_years": self.train_years,
            "end_date": end_eval_date.isoformat(),
            "strategy": self.strategy,
            "graham_mode": self.graham_mode
            if self.strategy in ("graham", "intelligent_investor")
            else None,
            "allocation": self.allocation,
            "initial_capital": init_capital_val,
            "sip_amount": self.sip_amount,
            "sip_mode": self.sip_mode,
            "screening_candidates": candidates,
            "portfolio_backtest": portfolio_results,
        }

        if self.out:
            write_json_report(self.out, combined_output)
            console.print(f"[green]Saved full report to {self.out}[/green]")

        if self.output_format == OutputFormat.JSON:
            print(json.dumps(combined_output, indent=2))
            return 0

        render_backtest_table(
            portfolio_results, asof_eval_date, end_eval_date, self.allocation, console
        )
        return 0

    @classmethod
    def _read_symbols_from_stdin(cls) -> list[str]:
        """Read a symbol stream from stdin; never blocks on a TTY."""
        try:
            return read_symbols()
        except Exception:  # noqa: BLE001
            # pytest and other harnesses raise instead of blocking; treat as "no pipe".
            return []

    _parse_symbols_stream = staticmethod(parse_symbols)


def main(argv: list[str] | None = None) -> Any:
    return ChainedScreenerSipExample().main(argv)


if __name__ == "__main__":
    sys.exit(main())
