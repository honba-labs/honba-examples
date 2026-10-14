"""02_fetch_instruments: pull the instrument master from a broker adapter.

Connects an adapter through the registry, downloads the instrument list (lot size, tick size,
kind, currency), optionally narrows it with a search query, and prints a summary. The default
adapter is ``fake``: deterministic and offline, so the example runs anywhere. Point it at a
real broker with ``--adapter dhan --config client_id=... --config access_token=...``.

Everything past the adapter boundary is a Honba ``Instrument``; no broker wire format shows up
here. Output is structured JSON for machine data-exchange, or a rich table in interactive terminals.

Run::

    python basic/02_fetch_instruments.py
    python basic/02_fetch_instruments.py --query RELIANCE --out instruments.json
    python basic/02_fetch_instruments.py --format json

Pitfalls::

    - Staleness: the master is whatever the adapter has cached or downloaded. The report has no as-
      of timestamp, and contracts expire, symbols change and lot sizes are revised, so a list from
      last week can describe instruments that no longer exist. Refresh daily and key on the exchange
      token, not the symbol text.
    - Lot and tick size: the report shows lot_size and tick_size but nothing here rounds to them. A
      derivatives quantity must be a multiple of the lot, and a price must sit on a tick; an order
      that ignores either is rejected, or worse, filled at an unintended size.
    - Search is a substring match and the table is sorted by symbol only: the same symbol on NSE and
      BSE, or RELIANCE and a similarly named series, appear together. Always filter on exchange and
      kind before picking an instrument.
    - Coverage: the fake serves a handful of instruments, so by_kind and count say nothing about a
      real master, which has tens of thousands of rows across segments.
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba.adapters.registry import default_registry
from honba.adapters.testing import FakeAdapter

from honba_examples.cli import add_out_arg, emit_json, make_parser


def _registry():
    registry = default_registry()
    if "fake" not in registry.available():
        registry.register("fake", FakeAdapter)
    return registry


def _row(instrument: Any) -> dict[str, Any]:
    iid = instrument.instrument_id
    return {
        "symbol": iid.symbol,
        "exchange": iid.exchange,
        "kind": str(getattr(instrument.kind, "value", instrument.kind)),
        "lot_size": instrument.lot_size,
        "tick_size": instrument.tick_size,
        "currency": instrument.currency,
    }


async def _fetch(adapter_name: str, query: str | None, config: dict[str, str]) -> list[Any]:
    adapter = _registry().create_market_data(adapter_name, **config)
    await adapter.connect()
    try:
        if query:
            return await adapter.search_instruments(query)
        return await adapter.instruments()
    finally:
        await adapter.disconnect()


def run(
    adapter: str = "fake",
    query: str | None = None,
    config: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Fetch instruments and return a structured summary."""
    instruments = asyncio.run(_fetch(adapter, query, config or {}))
    rows = sorted((_row(inst) for inst in instruments), key=lambda r: r["symbol"])
    return {
        "adapter": adapter,
        "query": query,
        "count": len(rows),
        "by_kind": dict(sorted(Counter(r["kind"] for r in rows).items())),
        "instruments": rows,
    }


def _parse_config(pairs: list[str]) -> dict[str, str]:
    config: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise SystemExit(f"--config expects KEY=VALUE, got {pair!r}")
        config[key] = value
    return config


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--adapter", default="fake", help="registered adapter name")
    parser.add_argument("--query", default=None, help="symbol substring to search for")
    parser.add_argument("--config", action="append", default=[], metavar="KEY=VALUE")
    add_out_arg(parser)
    parser.add_argument(
        "--format",
        choices=["auto", "table", "json"],
        default="auto",
        help="output format (default auto: rich table in interactive terminal, json for pipes/exchange)",
    )
    args = parser.parse_args(argv)

    result = run(args.adapter, args.query, _parse_config(args.config))

    is_table = (args.format == "table") or (
        args.format == "auto" and sys.stdout.isatty() and not args.out
    )

    if is_table:
        from honba.display import Column, render_kv, render_table

        render_kv([("Adapter", result["adapter"]), ("Total Instruments", result["count"])])
        cols = [
            Column("symbol", "Symbol", style="cyan"),
            Column("exchange", "Exchange", style="magenta"),
            Column("kind", "Kind", style="yellow"),
            Column("lot_size", "Lot Size", align="right", style="green"),
            Column("tick_size", "Tick Size", align="right", style="white"),
            Column("currency", "Currency", style="dim"),
        ]
        render_table(
            result["instruments"],
            cols,
            title=f"Instruments ({result['count']} loaded)",
        )
    else:
        emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
