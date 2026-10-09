"""02_fetch_instruments: pull the instrument master from a broker adapter.

Connects an adapter through the registry, downloads the instrument list (lot size, tick size,
kind, currency), optionally narrows it with a search query, and prints a summary. The default
adapter is ``fake``: deterministic and offline, so the example runs anywhere. Point it at a
real broker with ``--adapter dhan --config client_id=... --config access_token=...``.

Everything past the adapter boundary is a Honba ``Instrument``; no broker wire format shows up
here. Output is structured JSON so a research workflow or an LLM agent can consume it.

Run::

    python basic/02_fetch_instruments.py
    python basic/02_fetch_instruments.py --query RELIANCE --out instruments.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from honba.adapters.registry import default_registry
from honba.adapters.testing import FakeAdapter


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
    adapter: str = "fake", query: str | None = None, config: dict[str, str] | None = None
) -> dict[str, Any]:
    """Fetch instruments and return a JSON-serializable, deterministically ordered result."""
    instruments = asyncio.run(_fetch(adapter, query, config or {}))
    rows = sorted((_row(i) for i in instruments), key=lambda r: (r["symbol"], r["exchange"]))
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
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--adapter", default="fake", help="registered adapter name")
    parser.add_argument("--query", default=None, help="symbol substring to search for")
    parser.add_argument("--config", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON here")
    args = parser.parse_args(argv)

    result = run(args.adapter, args.query, _parse_config(args.config))
    text = json.dumps(result, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
