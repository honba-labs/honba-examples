"""backtesting/08_chained_screener_and_sip.py: Chained Screener and Forward SIP Backtest.

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
    python backtesting/08_chained_screener_and_sip.py --strategy multibagger --train-years 3 --asof 2024-01-01 --top 3 --capital 100000 --sip 10000

    # 2. Screen Benjamin Graham Defensive value candidates and run forward SIP:
    python backtesting/08_chained_screener_and_sip.py --strategy graham --graham-mode defensive --asof 2024-01-01 --top 3 --capital 100000 --sip 10000

    # 3. Equal allocation model benchmark:
    python backtesting/08_chained_screener_and_sip.py --strategy multibagger --asof 2024-01-01 --top 3 --capital 100000 --sip 10000 --allocation equal

    # 4. Prospective screening as of today (live forward monitoring):
    python backtesting/08_chained_screener_and_sip.py --strategy graham --screen-only --top 5
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
try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba.display import OutputFormat
from honba.strategies.base import Parameter, Strategy
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from honba_examples.base import HonbaExample
from honba_examples.screening import (
    find_catalog_path,
    get_latest_market_date,
    screen_intelligent_investor,
    screen_multibaggers,
    screen_net_net,
    screen_value_and_dividend,
)
from honba_examples.sip import simulate_portfolio_sip

__all__ = ["ChainedScreenerSipExample", "SipPortfolioStrategy", "main"]

logger = logging.getLogger(__name__)
console = Console()
err_console = Console(stderr=True)


class SipPortfolioStrategy(Strategy):
    """Systematic Investment Plan (SIP) strategy implementing Honba's Strategy ABC.

    Models monthly capital allocation and share accumulation on bar events.
    """

    name = "sip_portfolio_strategy"
    warmup_bars = 0

    monthly_amount = Parameter(
        default=10000.0,
        min=0.0,
        max=1000000.0,
        step=1000.0,
        description="Monthly SIP allocation amount in INR per stock",
    )
    initial_capital = Parameter(
        default=0.0,
        min=0.0,
        max=100000000.0,
        step=10000.0,
        description="Total initial capital budget in INR",
    )

    def __init__(self, monthly_amount: float = 10000.0, initial_capital: float = 0.0) -> None:
        super().__init__()
        self._monthly_amount = monthly_amount
        self._initial_capital = initial_capital
        self._last_month: int | None = None

    def on_bar(self, bar: Any) -> None:
        bar_date = getattr(bar, "date", None)
        if bar_date is None:
            return
        if self._last_month != bar_date.month and self._monthly_amount > 0:
            self._last_month = bar_date.month
            self.buy(bar.instrument_id, 1)


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
    screen_only: bool = False
    symbols: str | None = None
    out: Path | None = None

    def add_custom_args(self, parser: argparse.ArgumentParser) -> None:
        """Add screener and SIP specific options alongside HonbaExample base args."""
        parser.add_argument(
            "--strategy",
            choices=[
                "multibagger",
                "graham",
                "intelligent_investor",
                "net_net",
                "ncav",
                "dividend",
                "value",
                "all",
            ],
            default=self.strategy,
            help="Screener strategy: 'multibagger' (momentum), 'graham'/'intelligent_investor' (Benjamin Graham value), 'dividend', or 'value'",
        )
        parser.add_argument(
            "--graham-mode",
            choices=["defensive", "enterprising"],
            default=self.graham_mode,
            help="Graham strategy profile: 'defensive' (strict 22.5 Graham product, low D/E) or 'enterprising'",
        )
        parser.add_argument(
            "--asof",
            dest="asof_str",
            type=str,
            default=None,
            help="In-sample evaluation date (YYYY-MM-DD). If omitted, falls back to --start.",
        )
        parser.add_argument(
            "--end-date",
            dest="end_date_str",
            type=str,
            default=None,
            help="Terminal date for forward out-of-sample backtest (YYYY-MM-DD, alias for --end)",
        )
        parser.add_argument(
            "--train-years",
            type=int,
            default=self.train_years,
            help="Number of in-sample training years prior to asof date (default: 3)",
        )
        parser.add_argument(
            "--sip-amount",
            "--sip",
            dest="sip_amount",
            type=float,
            default=None,
            help="Monthly SIP installment in INR (default: ₹10,000 total portfolio SIP)",
        )
        parser.add_argument(
            "--sip-mode",
            dest="sip_mode",
            choices=["portfolio", "per-stock"],
            default=self.sip_mode,
            help="How to interpret --sip: 'portfolio' (default) or 'per-stock'",
        )
        parser.add_argument(
            "--allocation",
            choices=["52w-ratio", "equal", "score"],
            default=self.allocation,
            help="Portfolio capital allocation: '52w-ratio' (proximity to 52w high), 'equal', or 'score'",
        )
        parser.add_argument(
            "--top",
            type=int,
            default=self.top,
            help="Limit screening candidates to top N (default: 5)",
        )
        parser.add_argument(
            "--screen-only",
            action="store_true",
            help="Run Stage 1 screening only (do not run forward out-of-sample backtest)",
        )
        parser.add_argument(
            "--symbols",
            type=str,
            default=None,
            help=(
                "Explicit comma-separated symbols (skips screening). Use '-' to read the symbol "
                "stream from stdin, or pipe a screener into this script to auto-detect."
            ),
        )
        parser.add_argument(
            "--out",
            type=Path,
            default=None,
            help="Optional path to write JSON output report",
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
        if self.asof_str:
            try:
                asof_eval_date = dt.date.fromisoformat(self.asof_str)
            except ValueError:
                err_console.print(
                    f"[red]Error:[/red] Invalid date format for --asof: '{self.asof_str}'"
                )
                return 1
        else:
            asof_eval_date = self.start_date

        # Guard: if asof_eval_date is past latest available market date, snap to latest available date
        if asof_eval_date > latest_data_date:
            err_console.print(
                f"[dim]Note: Specified --asof date ({asof_eval_date}) is beyond latest catalog date ({latest_data_date}). "
                f"Snapping evaluation date to {latest_data_date}.[/dim]"
            )
            asof_eval_date = latest_data_date

        if self.end_date_str:
            try:
                end_eval_date = dt.date.fromisoformat(self.end_date_str)
            except ValueError:
                err_console.print(
                    f"[red]Error:[/red] Invalid date format for --end-date: '{self.end_date_str}'"
                )
                return 1
        elif self.end_date and self.end_date <= latest_data_date:
            end_eval_date = self.end_date
        else:
            end_eval_date = latest_data_date

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
            if self.strategy == "multibagger":
                candidates = screen_multibaggers(
                    catalog_dir=catalog_dir,
                    asof_date=asof_eval_date,
                    train_years=self.train_years,
                    top=self.top,
                )
            elif self.strategy in ("graham", "intelligent_investor"):
                candidates = screen_intelligent_investor(
                    catalog_dir=catalog_dir,
                    asof_date=asof_eval_date,
                    train_years=self.train_years,
                    mode=self.graham_mode,
                    top=self.top,
                )
            elif self.strategy in ("net_net", "ncav"):
                candidates = screen_net_net(
                    catalog_dir=catalog_dir,
                    asof_date=asof_eval_date,
                    train_years=self.train_years,
                    top=self.top,
                )
            else:
                candidates = screen_value_and_dividend(
                    catalog_dir=catalog_dir,
                    asof_date=asof_eval_date,
                    train_years=self.train_years,
                    mode=self.strategy,
                    top=self.top,
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
            candidates = [self._symbol_stub(s, asof_eval_date) for s in target_symbols]

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
            self._render_screener_table(candidates, asof_eval_date, self.strategy, self.train_years)

        # Check if out-of-sample forward backtest can be performed
        has_forward_window = asof_eval_date < end_eval_date

        # If --screen-only requested or no future forward window exists
        if self.screen_only or not has_forward_window:
            if self.output_format == OutputFormat.JSON:
                print(json.dumps({"stage": "screening", "candidates": candidates}, indent=2))
                return 0

            if not self.screen_only and not has_forward_window:
                console.print()
                console.print(
                    Panel(
                        f"Evaluation date ([cyan]{asof_eval_date}[/cyan]) is at the horizon of the historical catalog (latest session: [yellow]{latest_data_date}[/yellow]).\n"
                        "Forward out-of-sample data is not yet available for forward backtesting.\n\n"
                        f"The [bold cyan]{len(candidates)}[/bold cyan] screened candidates above represent prospective "
                        "'going-to-be' investments identified for [bold green]live forward monitoring[/bold green]!\n\n"
                        "[dim]💡 Tip: To simulate out-of-sample forward SIP confirmation, run with a historical evaluation date, e.g.:[/dim]\n"
                        f"   [bold green]python backtesting/08_chained_screener_and_sip.py --strategy {self.strategy} --asof 2024-01-01 --top {self.top} --capital 100000 --sip 10000[/bold green]",
                        title="Stage 2 Forward Confirmation Notice",
                        border_style="yellow",
                        box=box.ROUNDED,
                    )
                )
            return 0

        # Compute portfolio allocation weights
        weights: dict[str, float] = {}
        if self.allocation == "52w-ratio" and candidates:
            # Proximity to 52-week high: tighter consolidation base gets higher allocation
            raw_w = {
                c["symbol"]: 1.0 / max(abs(float(c.get("pct_from_high", -5.0))), 0.1)
                for c in candidates
            }
            tot_w = sum(raw_w.values())
            weights = {s: v / tot_w for s, v in raw_w.items()}
        elif self.allocation == "score" and candidates:
            raw_w = {
                c["symbol"]: max(float(c.get("margin_of_safety_pct", c.get("mb_score", 10.0))), 1.0)
                for c in candidates
            }
            tot_w = sum(raw_w.values())
            weights = {s: v / tot_w for s, v in raw_w.items()}
        elif self.allocation == "equal" and candidates:
            weights = {c["symbol"]: 1.0 / len(candidates) for c in candidates}

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
            self.out.parent.mkdir(parents=True, exist_ok=True)
            self.out.write_text(json.dumps(combined_output, indent=2) + "\n", encoding="utf-8")
            console.print(f"[green]Saved full report to {self.out}[/green]")

        if self.output_format == OutputFormat.JSON:
            print(json.dumps(combined_output, indent=2))
            return 0

        self._render_backtest_table(
            portfolio_results,
            asof_eval_date,
            end_eval_date,
            self.allocation,
        )
        return 0

    @staticmethod
    def _symbol_stub(symbol: str, asof_date: dt.date) -> dict[str, Any]:
        """Minimal candidate record for explicitly-provided or piped symbols."""
        return {
            "symbol": symbol,
            "company_name": symbol,
            "asof_date": asof_date.isoformat(),
            "close": 0.0,
            "ret_1y": 0.0,
            "ret_3m": 0.0,
            "train_ret_pct": 0.0,
            "pct_from_high": 0.0,
            "pct_from_low": 0.0,
            "low52": 0.0,
            "sma200": 0.0,
            "sma200_rising": True,
            "vol_surge": 1.0,
            "roce_pct": None,
            "debt_to_equity": None,
            "mb_score": 100.0,
            "pe_ratio": None,
            "pb_ratio": None,
            "graham_product": None,
            "graham_num": 0.0,
            "margin_of_safety_pct": 0.0,
            "current_ratio": None,
            "div_yield_pct": 0.0,
        }

    @classmethod
    def _read_symbols_from_stdin(cls) -> list[str]:
        """Read a symbol stream from stdin; never blocks on a TTY."""
        try:
            from honba_examples.pipeline.io import read_symbols as pipeline_read_symbols

            return pipeline_read_symbols()
        except Exception:  # noqa: BLE001
            # pytest and other harnesses raise instead of blocking; treat as "no pipe".
            return []

    @staticmethod
    def _parse_symbols_stream(raw: str) -> list[str]:
        from honba_examples.pipeline.io import parse_symbols as pipeline_parse_symbols

        return pipeline_parse_symbols(raw)

    def _render_screener_table(
        self,
        candidates: list[dict[str, Any]],
        asof_date: dt.date,
        strategy: str,
        train_years: int,
    ) -> None:
        """Render Rich terminal table for Stage 1 screened candidates."""
        if strategy == "multibagger":
            title = f"Stage 1 (In-Sample): Prospective 'Going-to-Be' Multibaggers as of {asof_date} ({train_years}-Yr Base Window)"
            table = Table(
                title=title,
                border_style="cyan",
                box=box.ROUNDED,
                header_style="bold cyan",
            )
            table.add_column("Symbol", style="bold cyan")
            table.add_column("Price (₹)", justify="right")
            table.add_column("1-Yr Ret", justify="right", style="green")
            table.add_column("3-Mo Surge", justify="right", style="bold green")
            table.add_column("From 52w High", justify="right", style="magenta")
            table.add_column("Vol Surge", justify="right", style="yellow")
            table.add_column("200 SMA", justify="right", style="dim")
            table.add_column("ROCE %", justify="right", style="yellow")
            table.add_column("MB Score", justify="right", style="bold cyan")

            for c in candidates:
                roce_s = f"{c['roce_pct']:.1f}%" if c.get("roce_pct") is not None else "-"
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
                    f"{c.get('mb_score', 0.0):.1f}",
                )
            console.print(table)
        elif strategy in ("graham", "intelligent_investor"):
            title = (
                f"Stage 1 (In-Sample): Benjamin Graham '{self.graham_mode.title()}' Value Candidates "
                f"as of {asof_date} ({train_years}-Yr Base Window)"
            )
            table = Table(
                title=title,
                border_style="cyan",
                box=box.ROUNDED,
                header_style="bold cyan",
            )
            table.add_column("Symbol", style="bold cyan", no_wrap=True)
            table.add_column("Company", style="dim")
            table.add_column("Price (₹)", justify="right", no_wrap=True)
            table.add_column("EPS (₹)", justify="right", style="green", no_wrap=True)
            table.add_column("BVPS (₹)", justify="right", style="dim", no_wrap=True)
            table.add_column("P/E", justify="right", no_wrap=True)
            table.add_column("P/B", justify="right", no_wrap=True)
            table.add_column("P/E×P/B", justify="right", style="yellow", no_wrap=True)
            table.add_column("Graham Val (₹)", justify="right", style="bold yellow", no_wrap=True)
            table.add_column("Margin of Safety", justify="right", style="bold green", no_wrap=True)
            table.add_column("Cur Ratio", justify="right", no_wrap=True)
            table.add_column("Debt/Eq", justify="right", no_wrap=True)
            table.add_column("Div Yield", justify="right", style="bold magenta", no_wrap=True)

            for c in candidates:
                pe_s = f"{c['pe_ratio']:.1f}" if c.get("pe_ratio") is not None else "-"
                pb_s = f"{c['pb_ratio']:.2f}" if c.get("pb_ratio") is not None else "-"
                gp_s = f"{c['graham_product']:.1f}" if c.get("graham_product") is not None else "-"
                cr_s = f"{c['current_ratio']:.2f}" if c.get("current_ratio") is not None else "-"
                de_s = f"{c['debt_to_equity']:.2f}" if c.get("debt_to_equity") is not None else "-"
                div_s = f"{c['div_yield_pct']:.2f}%" if c.get("div_yield_pct", 0) > 0 else "-"
                mos_s = (
                    f"+{c['margin_of_safety_pct']:.1f}%"
                    if c.get("margin_of_safety_pct", 0) >= 0
                    else f"{c['margin_of_safety_pct']:.1f}%"
                )
                comp_name = c.get("company_name", c["symbol"])
                table.add_row(
                    c["symbol"],
                    comp_name,
                    f"₹{c['close']:,.2f}",
                    f"₹{c['eps']:,.2f}",
                    f"₹{c['bvps']:,.2f}",
                    pe_s,
                    pb_s,
                    gp_s,
                    f"₹{c['graham_num']:,.2f}",
                    mos_s,
                    cr_s,
                    de_s,
                    div_s,
                )
            console.print(table)
        elif strategy in ("net_net", "ncav"):
            title = (
                f"Stage 1 (In-Sample): Benjamin Graham Net-Net (NCAV) Candidates as of {asof_date} "
                f"({train_years}-Yr Base Window)"
            )
            table = Table(
                title=title,
                border_style="cyan",
                box=box.ROUNDED,
                header_style="bold cyan",
            )
            table.add_column("Symbol", style="bold cyan", no_wrap=True)
            table.add_column("Company", style="dim")
            table.add_column("Price (₹)", justify="right", no_wrap=True)
            table.add_column("Cur Assets", justify="right", style="dim", no_wrap=True)
            table.add_column("Tot Liab", justify="right", style="dim", no_wrap=True)
            table.add_column("NCAV/Sh (₹)", justify="right", style="bold yellow", no_wrap=True)
            table.add_column("P/NCAV", justify="right", style="bold green", no_wrap=True)
            table.add_column("NCAV MoS", justify="right", no_wrap=True)
            table.add_column("Cur Ratio", justify="right", no_wrap=True)
            table.add_column("Cash/Sh (₹)", justify="right", style="magenta", no_wrap=True)
            table.add_column("Debt/Eq", justify="right", no_wrap=True)
            table.add_column("CFO (₹Cr)", justify="right", style="green", no_wrap=True)

            for c in candidates:
                cr_s = f"{c['current_ratio']:.2f}" if c.get("current_ratio") is not None else "-"
                de_s = f"{c['debt_to_equity']:.2f}" if c.get("debt_to_equity") is not None else "-"
                p_ncav_s = (
                    f"{c['price_to_ncav']:.2f}x" if c.get("price_to_ncav") is not None else "-"
                )
                mos_val = c.get("ncav_margin_of_safety_pct", 0)
                mos_style = "bold green" if mos_val >= 0 else "dim"
                mos_s = f"[{mos_style}]{mos_val:+.1f}%[/{mos_style}]"

                table.add_row(
                    c["symbol"],
                    c.get("company_name", c["symbol"]),
                    f"₹{c['close']:,.2f}",
                    f"₹{c.get('cur_assets_cr', 0):,.0f}Cr",
                    f"₹{c.get('tot_liab_cr', 0):,.0f}Cr",
                    f"₹{c.get('ncav_per_share', 0):,.2f}",
                    p_ncav_s,
                    mos_s,
                    cr_s,
                    f"₹{c.get('cash_per_share', 0):,.2f}",
                    de_s,
                    f"₹{c.get('cfo_cr', 0):,.0f}Cr",
                )
            console.print(table)
        else:
            title = f"Stage 1 (In-Sample): Screened {strategy.title()} Candidates as of {asof_date} ({train_years}-Yr Training Data)"
            table = Table(
                title=title,
                border_style="cyan",
                box=box.ROUNDED,
                header_style="bold cyan",
            )
            table.add_column("Symbol", style="bold cyan", no_wrap=True)
            table.add_column("Company", style="dim")
            table.add_column("Price (₹)", justify="right", no_wrap=True)
            table.add_column("52w Low (₹)", justify="right", style="dim", no_wrap=True)
            table.add_column("From 52w Low", justify="right", style="yellow", no_wrap=True)
            table.add_column(f"{train_years}-Yr Ret", justify="right", style="dim", no_wrap=True)
            table.add_column("Hit Low?", justify="center", style="bold green", no_wrap=True)
            table.add_column("Div Yield", justify="right", style="bold magenta", no_wrap=True)
            table.add_column("P/E", justify="right", no_wrap=True)
            table.add_column("ROCE %", justify="right", style="green", no_wrap=True)
            table.add_column("Debt/Eq", justify="right", no_wrap=True)

            for c in candidates:
                div_s = f"{c['div_yield_pct']:.2f}%" if c.get("div_yield_pct", 0) > 0 else "-"
                pe_s = f"{c['pe_ratio']:.1f}" if c.get("pe_ratio") is not None else "-"
                roce_s = f"{c['roce_pct']:.1f}%" if c.get("roce_pct") is not None else "-"
                de_s = f"{c['debt_to_equity']:.2f}" if c.get("debt_to_equity") is not None else "-"
                hit_s = "✓ Touch" if c.get("hit_52w_low_recently") else "Near"
                comp_name = c.get("company_name", c["symbol"])
                table.add_row(
                    c["symbol"],
                    comp_name,
                    f"₹{c['close']:,.2f}",
                    f"₹{c['low52']:,.2f}",
                    f"+{c['pct_from_low']:.1f}%",
                    f"{c['train_ret_pct']:+.1f}%",
                    hit_s,
                    div_s,
                    pe_s,
                    roce_s,
                    de_s,
                )
            console.print(table)

    @staticmethod
    def _render_backtest_table(
        portfolio: dict[str, Any],
        asof_date: dt.date,
        end_date: dt.date,
        allocation: str = "52w-ratio",
    ) -> None:
        """Render Rich terminal table and summary panel for Stage 2 out-of-sample forward backtest."""
        stocks: list[dict[str, Any]] = portfolio["stocks"]
        summary: dict[str, Any] = portfolio["summary"]
        init_capital = summary.get("initial_capital", 0.0)
        port_sip = summary.get("portfolio_monthly_sip", summary.get("monthly_sip", 0.0))
        tot_sip_invested = summary.get("total_sip_invested", 0.0)
        window_months = summary.get("window_months", 1)

        console.print()
        if init_capital > 0 and (tot_sip_invested > 0 or port_sip > 0):
            header = f"Stage 2 (Out-of-Sample Forward Confirmation): Initial Capital ₹{init_capital:,.0f} + Monthly SIP ₹{port_sip:,.0f}/mo ({asof_date} to {end_date})"
        elif init_capital > 0:
            header = f"Stage 2 (Out-of-Sample Forward Confirmation): Initial Capital ₹{init_capital:,.0f} Lump Sum ({asof_date} to {end_date})"
        else:
            header = f"Stage 2 (Out-of-Sample Forward Confirmation): Monthly SIP ₹{port_sip:,.0f}/mo ({asof_date} to {end_date})"

        table = Table(
            title=header,
            border_style="green",
            box=box.ROUNDED,
            header_style="bold green",
        )
        table.add_column("Symbol", style="bold cyan")
        table.add_column("Weight", justify="right", style="cyan")
        table.add_column("Months", justify="center")
        table.add_column("SIP / Mo", justify="right", style="dim")
        table.add_column("Total Invested", justify="right")
        table.add_column("Final Valuation", justify="right", style="bold green")
        table.add_column("Net Profit (₹)", justify="right")
        table.add_column("SIP Mult", justify="right", style="bold yellow")
        table.add_column("Lump Mult", justify="right", style="bold green")
        table.add_column("Peak Mult", justify="right", style="magenta")
        table.add_column("XIRR (p.a.)", justify="right", style="bold magenta")
        table.add_column("Confirmation Status", justify="center", style="bold cyan")

        confirmed_count = 0
        for r in stocks:
            profit_style = "green" if r["profit"] >= 0 else "red"
            xirr_str = f"{r['xirr_pct']:+.1f}%" if r["xirr_pct"] is not None else "N/A"
            conf = r.get("confirmation", "N/A")
            if "Multibagger" in conf:
                confirmed_count += 1
                conf_style = "bold green"
            elif "Outperformer" in conf:
                conf_style = "green"
            else:
                conf_style = "dim"

            row_data = [
                r["symbol"],
                f"{r.get('weight_pct', 0.0):.1f}%",
                str(r["months"]),
                f"₹{r.get('sip_amount', 0.0):,.0f}",
                f"₹{r['total_invested']:,.0f}",
                f"₹{r['final_val']:,.0f}",
                f"[{profit_style}]₹{r['profit']:+,.0f}[/{profit_style}]",
                f"{r['sip_multiple']:.2f}x",
                f"{r['lump_multiple']:.2f}x",
                f"{r.get('peak_multiple', r['lump_multiple']):.2f}x",
                xirr_str,
                f"[{conf_style}]{conf}[/{conf_style}]",
            ]
            table.add_row(*row_data)

        console.print(table)

        p_xirr_str = (
            f"{summary['portfolio_xirr']:+.1f}% p.a."
            if summary["portfolio_xirr"] is not None
            else "N/A"
        )
        p_profit_pct = (
            (summary["total_profit"] / summary["total_invested"]) * 100.0
            if summary["total_invested"] > 0
            else 0.0
        )
        conf_rate = (
            f"{confirmed_count}/{len(stocks)} ({(confirmed_count / len(stocks)) * 100:.0f}%)"
            if stocks
            else "0/0"
        )

        alloc_desc = (
            "Proximity to 52-Week High (Ratio to Low from 52w High)"
            if allocation == "52w-ratio"
            else (
                "Composite Multibagger Score" if allocation == "score" else "Equal Weighting (1/N)"
            )
        )

        panel_lines = [
            f"[bold]In-Sample Screening Date :[/bold] [cyan]{asof_date}[/cyan]",
            f"[bold]Out-of-Sample Test Window:[/bold] [yellow]{asof_date}[/yellow] to [yellow]{end_date}[/yellow] ({window_months} months)",
            f"[bold]Capital Allocation Model :[/bold] [cyan]{alloc_desc}[/cyan]",
        ]
        if init_capital > 0:
            panel_lines.append(
                f"[bold]Initial Capital Deployed :[/bold] ₹{init_capital:,.2f} (invested on {asof_date} across {len(stocks)} stocks)"
            )
        if tot_sip_invested > 0 or port_sip > 0:
            panel_lines.append(
                f"[bold]Monthly SIP Installment  :[/bold] ₹{port_sip:,.2f}/month portfolio over {window_months} months"
            )
            panel_lines.append(
                f"[bold]Total SIP Invested       :[/bold] ₹{tot_sip_invested:,.2f} ({window_months} months × ₹{port_sip:,.2f})"
            )
        panel_lines.extend(
            [
                f"[bold]Total Capital Invested   :[/bold] [bold]₹{summary['total_invested']:,.2f}[/bold] (₹{init_capital:,.0f} initial + {window_months} × ₹{port_sip:,.0f} SIP)",
                f"[bold]Terminal Portfolio Value :[/bold] [bold green]₹{summary['total_value']:,.2f}[/bold green]",
                f"[bold]Total Net Profit         :[/bold] [bold green]₹{summary['total_profit']:+,.2f}[/bold green] ([green]{p_profit_pct:+.1f}%[/green])",
                f"[bold]Overall Portfolio Mult   :[/bold] [bold yellow]{summary['portfolio_multiple']:.2f}x[/bold yellow]",
                f"[bold]Portfolio Annual XIRR    :[/bold] [bold magenta]{p_xirr_str}[/bold magenta]",
                f"[bold]Multibagger Confirmation :[/bold] [bold green]{conf_rate} confirmed as >=2x/3x multibaggers![/bold green]",
            ]
        )

        console.print()
        console.print(
            Panel(
                "\n".join(panel_lines),
                title="In-Sample Hypothesis vs Out-of-Sample SIP Confirmation",
                border_style="magenta",
                box=box.ROUNDED,
            )
        )


def main(argv: list[str] | None = None) -> Any:
    return ChainedScreenerSipExample().main(argv)


if __name__ == "__main__":
    sys.exit(main())
