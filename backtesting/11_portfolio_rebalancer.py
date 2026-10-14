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
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba.display import OutputFormat
from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from rich import box
from rich.console import Console
from rich.table import Table

from honba_examples.cli import add_out_arg
from honba_examples.ewr import FEE_RATE, REBALANCE_DAYS, SimResult, simulate
from honba_examples.pipeline import SipBacktestHarness
from honba_examples.pipeline.base import Pipeline, as_date
from honba_examples.pipeline.io import read_symbols
from honba_examples.screener_cli import ScreenerCLIExample  # noqa: F401
from honba_examples.screening import find_catalog_path, get_latest_market_date
from honba_examples.settlement import resolve_settlement_days
from honba_examples.sip import calculate_xirr, sip_data_notes

__all__ = ["PortfolioRebalancerExample", "load_bars", "main", "union_screens"]

console = Console()
err_console = Console(stderr=True)


def union_screens(results: list[tuple[list[str], list[dict[str, Any]]]]) -> tuple[list[str], list]:
    """Merge screener outputs: symbols in first-seen order, one candidate row per symbol."""
    symbols: list[str] = []
    candidates: dict[str, dict[str, Any]] = {}
    for syms, cands in results:
        for sym in syms:
            if sym not in candidates:
                symbols.append(sym)
                candidates[sym] = next(
                    (c for c in cands if c.get("symbol") == sym), {"symbol": sym}
                )
    return symbols, [candidates[s] for s in symbols]


def load_bars(symbols: list[str], start: dt.date, end: dt.date, catalog_dir: Path) -> list[Bar]:
    """Daily bars for ``symbols`` in ``[start, end]`` from ``<catalog_dir>/<SYMBOL>/*.parquet``.

    Symbols without a directory or without bars in the window simply contribute nothing.
    """
    import duckdb

    bars: list[Bar] = []
    con = duckdb.connect()
    try:
        for sym in symbols:
            files = [str(f) for f in sorted((catalog_dir / sym).glob("*.parquet"))]
            if not files:
                continue
            rows = con.execute(
                "SELECT ts, open, high, low, close, volume FROM read_parquet(?) "
                "WHERE epoch_ms(ts // 1000000)::DATE BETWEEN ? AND ? ORDER BY ts",
                [files, start, end],
            ).fetchall()
            iid = InstrumentId(sym, "NSE")
            bars.extend(Bar(iid, int(r[0]), r[1], r[2], r[3], r[4], r[5]) for r in rows)
    finally:
        con.close()
    bars.sort(key=lambda b: (b.ts, b.instrument_id.symbol))
    return bars


class PortfolioRebalancerExample:
    """Equal-weight periodic rebalancer: stdin-aware symbols over the ``ewr`` simulator."""

    top: int = 5
    strategy: str = "multibagger"
    asof_str: str | None = None
    end_date_str: str | None = None
    capital: float = 100000.0
    sip_amount: float | None = None
    allocation: str = "equal"
    symbols: str | None = None
    rebalance_days: int = REBALANCE_DAYS
    fee: float = FEE_RATE
    output_format: OutputFormat = OutputFormat.TABLE

    def add_custom_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--strategy",
            choices=["multibagger", "value", "dividend", "all"],
            default=self.strategy,
            help="screener when symbols are not supplied; 'all' unions the three (default: "
            "multibagger)",
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
            help="initial capital deployed on the first session after --asof (INR)",
        )
        parser.add_argument(
            "--sip-amount",
            type=float,
            default=None,
            help="rupees added at every rebalance session (INR; optional), not monthly",
        )
        parser.add_argument(
            "--allocation",
            choices=["equal", "52w-ratio", "score"],
            default=self.allocation,
            help="equal: periodic equal-weight rebalance (default); 52w-ratio / score: one-shot "
            "weighted buy-and-hold with no rebalancing",
        )
        parser.add_argument(
            "--rebalance-days",
            type=int,
            default=self.rebalance_days,
            help=f"calendar days between rebalances (default: {REBALANCE_DAYS})",
        )
        parser.add_argument(
            "--fee",
            type=float,
            default=self.fee,
            help=f"flat fraction of traded notional, per side (default: {FEE_RATE})",
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
        parser.add_argument(
            "--data-dir",
            type=Path,
            default=None,
            help="data root holding catalog/1D/NSE (default: the workspace data root)",
        )
        add_out_arg(parser)
        parsed = parser.parse_args(argv)
        for key, value in vars(parsed).items():
            if hasattr(self, key):
                setattr(self, key, value)
        # keep the extra parsed attrs for --out/--data-dir
        self._extra_data_dir: Path | None = getattr(parsed, "data_dir", None)
        self._extra_out: Path | None = getattr(parsed, "out", None)
        return parsed

    def _catalog_dir(self) -> Path:
        if self._extra_data_dir is not None:
            return find_catalog_path(Path(self._extra_data_dir) / "catalog" / "1D" / "NSE")
        return find_catalog_path()

    def _screen(self, asof: str | None) -> tuple[list[str], list[dict[str, Any]]]:
        """Run the chosen screener(s); ``all`` is the union of the three, ``--top`` names each."""
        from honba_examples.pipeline.screeners import (
            DividendYieldScreener,
            MultibaggerScreener,
            ValueScreener,
        )

        factories = {
            "multibagger": MultibaggerScreener,
            "value": ValueScreener,
            "dividend": DividendYieldScreener,
        }
        names = list(factories) if self.strategy == "all" else [self.strategy]
        outputs = []
        for name in names:
            res = Pipeline([factories[name](asof=asof, top=self.top)]).run({})
            outputs.append((list(res.get("symbols") or []), list(res.get("candidates") or [])))
        return union_screens(outputs)

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

        allocation = self.allocation
        if syms is not None:
            syms = syms[: self.top] if self.top and self.top > 0 else syms
            candidates: list[dict[str, Any]] = [{"symbol": s} for s in syms]
            if allocation != "equal":
                err_console.print(
                    f"[yellow]--allocation {allocation} needs screener fields; supplied symbols "
                    "are weighted equally and rebalanced.[/yellow]"
                )
                allocation = "equal"
        else:
            syms, candidates = self._screen(self.asof_str)

        if not syms:
            err_console.print("[yellow]No candidates for the requested screener / asof.[/yellow]")
            return 0

        catalog_dir = self._catalog_dir()
        asof = as_date(self.asof_str) or get_latest_market_date(catalog_dir)
        end = as_date(self.end_date_str) or max(asof, get_latest_market_date(catalog_dir))
        if end <= asof:
            err_console.print(
                f"[yellow]No forward window: --end-date {end} is not after the screening date "
                f"{asof}.[/yellow]"
            )
            return 0

        if allocation == "equal":
            payload = self._rebalance(syms, candidates, asof, end)
        else:
            payload = self._buy_and_hold(syms, candidates, asof, end, catalog_dir, allocation)
        return self._emit(payload)

    # -- engines ---------------------------------------------------------------
    def _rebalance(
        self, syms: list[str], candidates: list[dict[str, Any]], asof: dt.date, end: dt.date
    ) -> dict[str, Any]:
        """Periodic equal-weight rebalance with ``honba_examples.ewr.simulate``.

        The first session strictly after ``asof`` is day 0, so the screen's own close is never
        traded. Fills are at session closes with a flat per-side fee and a T+N sell-to-buy lag.
        """
        start = asof + dt.timedelta(days=1)
        bars = load_bars(syms, start, end, self._catalog_dir())
        days, _ = resolve_settlement_days("NSE", as_of=start)
        result = simulate(
            bars,
            syms,
            start=start,
            capital=float(self.capital),
            rebalance_days=self.rebalance_days,
            sip_amount=float(self.sip_amount or 0.0),
            fee=self.fee,
            settlement_days=days,
        )
        return _rebalance_payload(self, syms, candidates, asof, end, days, result, bars)

    def _buy_and_hold(
        self,
        syms: list[str],
        candidates: list[dict[str, Any]],
        asof: dt.date,
        end: dt.date,
        catalog_dir: Path,
        allocation: str,
    ) -> dict[str, Any]:
        """One-shot weighted lump sum (plus optional monthly SIP); never rebalances."""
        harness = SipBacktestHarness(
            asof=asof,
            end_date=end,
            initial_capital=float(self.capital),
            monthly_sip=float(self.sip_amount) if self.sip_amount is not None else None,
            allocation=allocation,
            catalog_dir=catalog_dir,
        )
        out = harness.run({"symbols": syms, "candidates": candidates})
        return {
            "strategy": self.strategy,
            "mode": "buy_and_hold",
            "allocation": allocation,
            "rebalanced": False,
            "asof": asof.isoformat(),
            "symbols": syms,
            "candidates": candidates,
            "weights": {s["symbol"]: s["weight_pct"] / 100.0 for s in out["stocks"]},
            "summary": out["summary"],
            "stocks": out["stocks"],
            "notes": [
                (
                    f"--allocation {allocation} is a one-shot weighted lump sum held to the end: "
                    "the periodic rebalancer is equal-weight only, so nothing is rebalanced."
                ),
                *sip_data_notes(out["summary"]),
            ],
        }

    # -- output ----------------------------------------------------------------
    def _emit(self, payload: dict[str, Any]) -> int:
        fmt = self.output_format
        if fmt is OutputFormat.JSON or str(fmt).lower() == "json":
            text = json.dumps(payload, indent=2, default=str)
            print(text)
            self._write_out(text)
            return 0
        if fmt is OutputFormat.PLAIN:
            print(" ".join(str(s) for s in payload["symbols"]))
            return 0

        summary: dict[str, Any] = payload["summary"]
        stocks: list[dict[str, Any]] = payload["stocks"]
        rebalanced = payload["rebalanced"]
        title = "Equal-Weight Rebalance" if rebalanced else "Buy-and-Hold (no rebalancing)"
        table = Table(
            title=f"{title} — {len(stocks)} symbols (asof {payload.get('asof')})",
            box=box.ROUNDED,
            header_style="bold cyan",
            border_style="cyan",
        )
        table.add_column("Symbol", style="bold cyan")
        table.add_column("Weight %", justify="right")
        if rebalanced:
            table.add_column("Shares", justify="right")
            table.add_column("Final Price", justify="right")
            table.add_column("Final Value", justify="right", style="bold green")
            for s in stocks:
                table.add_row(
                    str(s["symbol"]),
                    f"{float(s['weight_pct']):.1f}",
                    f"{s['shares_held']:,}",
                    f"{float(s['final_price']):,.2f}",
                    f"{float(s['final_val']):,.0f}",
                )
        else:
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
        xirr = summary.get("portfolio_xirr")
        xirr_str = f"{float(xirr):+.1f}%" if xirr is not None else "N/A"
        line = (
            f"Invested ₹{float(summary.get('total_invested', 0.0)):,.0f} | "
            f"Value ₹{float(summary.get('total_value', 0.0)):,.0f} | "
            f"Profit ₹{float(summary.get('total_profit', 0.0)):+,.0f} | "
            f"Multiple {float(summary.get('portfolio_multiple', 1.0)):.2f}x | "
            f"XIRR {xirr_str}"
        )
        if rebalanced:
            line += (
                f" | Rebalances {summary['n_rebalances']} | Fees ₹{summary['total_fees']:,.0f} "
                f"| T+{summary['settlement_days']}"
            )
        console.print(line)
        for note in payload.get("notes", []):
            console.print(f"[yellow]⚠ {note}[/yellow]")
        self._write_out(json.dumps(payload, indent=2, default=str))
        return 0

    def _write_out(self, text: str) -> None:
        if self._extra_out:
            self._extra_out.parent.mkdir(parents=True, exist_ok=True)
            self._extra_out.write_text(text + "\n")

    def main(self, argv: list[str] | None = None) -> int:
        if argv is None:
            argv = sys.argv[1:]
        if not hasattr(self, "_extra_out"):
            self._extra_out = None
        if not hasattr(self, "_extra_data_dir"):
            self._extra_data_dir = None
        self._parse_argv(argv)
        return self.run(argv)


def _xirr(result: SimResult, final_value: float) -> float | None:
    """Annualised IRR (%) of the capital and every SIP injection against the final value."""
    if not result.equity_curve:
        return None
    first = dt.date.fromisoformat(result.equity_curve[0]["date"])
    flows = [(first, -float(result.initial_corpus))]
    for r in result.rebalances:
        if r.get("sip_injected"):
            flows.append((dt.date.fromisoformat(r["date"]), -float(r["sip_injected"])))
    flows.append((dt.date.fromisoformat(result.equity_curve[-1]["date"]), final_value))
    return calculate_xirr(flows)


def _rebalance_payload(
    ex: PortfolioRebalancerExample,
    syms: list[str],
    candidates: list[dict[str, Any]],
    asof: dt.date,
    end: dt.date,
    settlement_days: int,
    result: SimResult,
    bars: list[Bar],
) -> dict[str, Any]:
    last_price: dict[str, float] = {}
    for b in bars:  # time-ordered, so the last write is the final close
        last_price[b.instrument_id.symbol] = b.close
    last = result.equity_curve[-1] if result.equity_curve else {"value": 0.0, "cash": 0.0}
    value, cash = float(last["value"]), float(last["cash"])
    invested = float(result.total_invested) if result.equity_curve else 0.0
    stocks = [
        {
            "symbol": s,
            "weight_pct": round(qty * last_price[s] / value * 100.0, 1) if value > 0 else 0.0,
            "shares_held": qty,
            "final_price": last_price[s],
            "final_val": qty * last_price[s],
        }
        for s, qty in sorted(
            result.final_holdings.items(), key=lambda kv: -kv[1] * last_price[kv[0]]
        )
    ]
    metrics = result.metrics
    notes = list(result.notes)
    if result.missing_data:
        notes.append(f"No bars after {asof} for {', '.join(result.missing_data)}: not traded.")
    if result.never_held:
        notes.append(
            f"Never held (one share costs more than the target): {', '.join(result.never_held)}."
        )
    notes.append(
        "Flat fee per side and fills at session closes (no STT/stamp/DP cost pack, no slippage)."
    )
    return {
        "strategy": ex.strategy,
        "mode": "rebalance",
        "allocation": "equal",
        "rebalanced": True,
        "asof": asof.isoformat(),
        "symbols": syms,
        "candidates": candidates,
        "weights": {s: 1.0 / len(syms) for s in syms},
        "summary": {
            "candidate_count": len(syms),
            "start_date": result.equity_curve[0]["date"] if result.equity_curve else None,
            "end_date": end.isoformat(),
            "initial_capital": float(ex.capital),
            "sip_per_rebalance": float(ex.sip_amount or 0.0),
            "total_invested": invested,
            "total_value": value,
            "final_cash": cash,
            "total_profit": value - invested,
            "portfolio_multiple": value / invested if invested > 0 else 1.0,
            "portfolio_xirr": _xirr(result, value),
            "n_rebalances": int(metrics.get("n_rebalances", 0)),
            "total_fees": float(metrics.get("total_fees", 0.0)),
            "fee": ex.fee,
            "rebalance_days": ex.rebalance_days,
            "settlement_days": settlement_days,
            "max_drawdown_pct": metrics.get("max_drawdown_pct"),
            "missing_data": result.missing_data,
            "never_held": result.never_held,
        },
        "stocks": stocks,
        "rebalances": result.rebalance_changes,
        "notes": notes,
    }


def main(argv: list[str] | None = None) -> int:
    return PortfolioRebalancerExample().main(argv)


if __name__ == "__main__":
    sys.exit(main())
