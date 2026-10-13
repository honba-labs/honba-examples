"""Shared CLI base for standalone point-in-time screener scripts.

Screeners are one-job tools that read the point-in-time Parquet catalog (and optionally the
fundamentals DuckDB store), print a pure symbol/JSON stream on ``stdout`` and any diagnostics on
``stderr`` so they compose in Unix pipes:

    python screening/01_multibagger_screener.py --asof 2023-01-01 --top 5 --format symbols \\
        | python backtesting/10_chained_screener_and_sip.py --asof 2023-01-01 --sip-amount 10000

Supported ``--format`` values: ``table`` (rich), ``json`` (machine), ``csv`` (machine),
``symbols`` (space-separated), ``plain`` (newline-separated). Subclasses implement ``screen()``
and optionally ``render_table()`` and ``json_payload()``.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from honba_examples.base import HonbaExample
from honba_examples.pipeline.io import parse_symbols  # noqa: F401
from honba_examples.screening import find_catalog_path, get_latest_market_date

__all__ = ["ScreenerCLIExample"]

_FORMAT_CHOICES = ("table", "json", "csv", "symbols", "plain")


class ScreenerCLIExample(HonbaExample):
    """Base class for screener CLIs with a stream-friendly I/O contract."""

    date_range_args: bool = False
    stream_format: str = "table"
    output_width: int | None = None
    asof_str: str | None = None

    def add_common_args(self, parser: argparse.ArgumentParser) -> None:
        """Common screener args: catalog selection, evaluation date and output format.

        Overrides ``HonbaExample.add_common_args`` because screeners emit symbol/JSON streams
        rather than the standard table/plain metric blocks.
        """
        parser.add_argument(
            "--exchange", default=self.exchange, help="Exchange code (default: NSE)"
        )
        parser.add_argument(
            "--timeframe", default=self.timeframe, help="Bar timeframe (default: 1D)"
        )
        parser.add_argument("--data-dir", type=Path, default=self.data_dir, help="Data directory")
        parser.add_argument("--out-dir", type=Path, default=self.out_dir, help="Output directory")
        parser.add_argument(
            "--asof",
            dest="asof_str",
            type=str,
            default=None,
            help="In-sample evaluation date (YYYY-MM-DD). Defaults to the latest catalog session.",
        )
        parser.add_argument(
            "--format",
            dest="stream_format",
            choices=_FORMAT_CHOICES,
            default=self.stream_format,
            help="Output format: table, json, csv, symbols (space-separated), plain (newline)",
        )
        parser.add_argument(
            "--width", dest="output_width", type=int, default=None, help="Fixed width"
        )

    def resolve_catalog_dir(self) -> Path:
        """Locate the 1D Parquet catalog (``--data-dir`` if given, else the workspace data root)."""
        if self.data_dir:
            return find_catalog_path(self.data_dir)
        candidate = Path.cwd() / "honba-data" / "data" / "catalog" / self.timeframe / self.exchange
        return candidate if candidate.is_dir() else find_catalog_path()

    def resolve_asof(self, catalog_dir: Path) -> dt.date:
        """Resolve the point-in-time evaluation date, snapping past dates to the catalog horizon."""
        latest = get_latest_market_date(catalog_dir)
        if self.asof_str:
            try:
                asof = dt.date.fromisoformat(self.asof_str)
            except ValueError:
                print(f"Error: invalid --asof date: {self.asof_str!r}", file=sys.stderr)
                raise SystemExit(2)
        else:
            asof = latest
        if asof > latest:
            print(
                f"Note: --asof ({asof}) is beyond the latest catalog date ({latest}); "
                f"snapping to {latest}.",
                file=sys.stderr,
            )
            asof = latest
        return asof

    def screen(self, catalog_dir: Path, asof: dt.date) -> list[dict[str, Any]]:
        """Run the point-in-time screen. Subclasses must implement."""
        raise NotImplementedError("Subclasses must implement screen()")

    def render_table(self, candidates: list[dict[str, Any]], asof: dt.date) -> None:
        """Render a Rich table (interactive default). Subclasses must implement."""
        raise NotImplementedError("Subclasses must implement render_table()")

    def json_payload(self, candidates: list[dict[str, Any]], asof: dt.date) -> dict[str, Any]:
        """Machine-readable JSON document for ``--format json``."""
        return {"asof_date": asof.isoformat(), "candidates": candidates}

    def _emit(self, candidates: list[dict[str, Any]], asof: dt.date) -> None:
        fmt = str(self.stream_format)
        symbols = [c["symbol"] for c in candidates]

        if fmt == "plain":
            for symbol in symbols:
                print(symbol)
        elif fmt == "symbols":
            print(" ".join(symbols))
        elif fmt == "json":
            print(json.dumps(self.json_payload(candidates, asof), indent=2, default=str))
        elif fmt == "csv":
            columns = list(candidates[0]) if candidates else ["symbol"]
            writer = csv.DictWriter(sys.stdout, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            for row in candidates:
                writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in columns})
        else:
            self.render_table(candidates, asof)

    def run(self) -> Any:
        try:
            catalog_dir = self.resolve_catalog_dir()
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        asof = self.resolve_asof(catalog_dir)
        candidates = self.screen(catalog_dir, asof)

        if not candidates:
            print(
                f"No candidates found as of {asof}.",
                file=sys.stderr,
            )
            if str(self.stream_format) in ("plain", "symbols"):
                # Emit an empty stream so downstream pipes see EOF cleanly.
                pass
            return 0

        self._emit(candidates, asof)
        return 0


def rich_console() -> Any:
    """Lazily import and return a Rich console (keeps ``rich`` out of the machine path)."""
    from rich.console import Console

    return Console()
