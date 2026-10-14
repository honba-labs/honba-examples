"""screening/06_run_chain.py: Run a screen -> filter -> size -> execute chain from a TOML file.

Each ``[[steps]]`` entry names a step by manifest id (``uses = "screen.value"``) and may set
``with`` (param overrides) and ``report`` (extra columns to show, such as ``market_cap``, which
the step does not filter on). The chain is checked against the honba-strategies step schema
before anything runs; ``--describe`` prints the manifest of every available step.

Usage:
    # Validate a chain without running it
    python screening/06_run_chain.py --chain screening/chains/quality_value.toml --validate-only

    # Run it and print the surviving candidates as CSV
    python screening/06_run_chain.py --chain screening/chains/quality_value.toml --format csv

    # List the available steps, their params and reportable columns
    python screening/06_run_chain.py --describe

Pitfalls::

    - Each step keeps its own point-in-time rules: screen steps use fiscal_date <= asof (period
      end, no filing-date column) and no step manifest exposes a fiscal lag. report columns use
      the same rule at the as-of date. A chain that validates is still only a well-formed chain,
      not a lookahead-free one.
    - Order truncates: in quality_value.toml screen.value keeps its top 10 first and
      filter.fundamental_quality then prunes that list, so you do not get the best 10 that pass
      the quality test and the survivors can be very few. The filter logs kept/total at INFO level.
    - Missing values pass: FundamentalQualityFilter keeps a candidate whose ROCE, debt/equity or
      operating margin is missing, so a quality filter can pass names it never measured.
    - Hand-set parameters and small samples: P/E 25, P/B 3, ROCE 12 and the other values in the
      TOML were chosen by hand; editing the chain until the backtest looks good is data snooping.
      A SIP backtest of a few survivors is anecdote, not evidence.
    - Lookahead and holes at the join: the screen reads the as-of close and execute.sip_backtest
      buys at that day's open; symbols with no bars after the as-of date are valued flat at their
      last close and symbols with no data are skipped.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba.strategies.loader import CatalogError

from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.pipeline.chain import (
    ChainError,
    describe_all,
    load_chain,
    run_chain,
    validate,
)

__all__ = ["main", "run"]

_FORMATS = ("table", "json", "csv", "symbols", "plain")


def run(chain: list[dict[str, Any]], data_dir: Path | None = None) -> dict[str, Any]:
    """Validate and run ``chain``; returns the JSON-ready result payload."""
    result = run_chain(chain, catalog_dir=data_dir)
    asof = result.get("asof")
    payload: dict[str, Any] = {
        "asof_date": asof.isoformat() if isinstance(asof, dt.date) else None,
        "chain": [step["uses"] for step in chain],
        "symbols": result.symbols,
        "candidates": result.candidates,
    }
    if result.weights:
        payload["weights"] = result.weights
    if result.summary is not None:
        payload["summary"] = result.summary
        payload["stocks"] = result.stocks
    return payload


def _print_rows(payload: dict[str, Any], fmt: str) -> None:
    rows = payload["candidates"]
    if fmt == "plain":
        print("\n".join(payload["symbols"]))
    elif fmt == "symbols":
        print(" ".join(payload["symbols"]))
    elif fmt == "csv":
        columns = list(rows[0]) if rows else ["symbol"]
        writer = csv.DictWriter(sys.stdout, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in columns})
    else:
        from rich import box
        from rich.console import Console
        from rich.table import Table

        table = Table(title=" > ".join(payload["chain"]), box=box.ROUNDED, header_style="bold cyan")
        columns = list(rows[0]) if rows else ["symbol"]
        for name in columns:
            table.add_column(name, overflow="fold")
        for row in rows:
            table.add_row(*("-" if row.get(k) is None else str(row.get(k)) for k in columns))
        Console().print(table)


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--chain", type=Path, help="chain TOML file ([[steps]] uses/with/report)")
    parser.add_argument("--validate-only", action="store_true", help="check the chain, do not run")
    parser.add_argument("--describe", action="store_true", help="print step manifests as JSON")
    parser.add_argument("--format", choices=_FORMATS, default="table", help="candidate output")
    parser.add_argument("--data-dir", type=Path, default=None, help="override catalog directory")
    add_out_arg(parser)
    args: argparse.Namespace = parser.parse_args(argv)

    if args.describe:
        emit_json(describe_all(), args.out)
        return 0
    if args.chain is None:
        parser.error("--chain is required (or use --describe)")
    try:
        chain = load_chain(args.chain)
        errors = validate(chain)
        if errors:
            raise ChainError(errors)
        if args.validate_only:
            print(f"chain valid: {' > '.join(step['uses'] for step in chain)}")
            return 0
        payload = run(chain, args.data_dir)
    except (ChainError, CatalogError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.format == "json":
        emit_json(payload, args.out)
    else:
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(payload, indent=2, default=str) + "\n")
        _print_rows(payload, args.format)
    return 0


if __name__ == "__main__":
    sys.exit(main())
