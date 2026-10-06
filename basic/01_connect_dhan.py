"""01_connect_dhan: connect to a broker through Honba's adapter registry.

An adapter is looked up by name in ``honba.adapters.registry``, connected, and asked for
the two things a runner needs before it trades: the ``SessionInfo`` it authenticated with
and the ``AdapterCapabilities`` that bound what it can do. The default adapter is
``fake``: deterministic and offline, so the example runs anywhere. Point it at a real
broker with ``--adapter dhan --config client_id=... --config access_token=...``.

Everything past the adapter boundary is a Honba type; no broker wire format shows up
here. Output is structured JSON so a research workflow or an LLM agent can consume it.

Run::

    python basic/01_connect_dhan.py
    python basic/01_connect_dhan.py --out connection.json
    python basic/01_connect_dhan.py --adapter dhan --config client_id=... --config access_token=...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from honba.adapters.errors import AdapterNotFound
from honba.adapters.registry import default_registry
from honba.adapters.testing import FakeAdapter


def _registry():
    registry = default_registry()
    if "fake" not in registry.available():
        registry.register("fake", FakeAdapter)
    return registry


def _json(value: Any) -> Any:
    """Plain JSON image of a Honba value: enums by value, containers sorted, dataclasses
    as dicts. Deterministic, so two runs of the same adapter dump the same bytes."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (set, frozenset, tuple, list)):
        return sorted(_json(item) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _json(item) for key, item in asdict(value).items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


async def _connect(adapter_name: str, config: dict[str, str]) -> dict[str, Any]:
    adapter = _registry().create(adapter_name, **config)
    session = await adapter.connect()
    try:
        report = {
            "adapter": adapter_name,
            "connected": adapter.is_connected(),
            "adapter_class": type(adapter).__name__,
            "session": _json(session),
            "capabilities": _json(adapter.capabilities()),
        }
    finally:
        await adapter.disconnect()
    return report


def run(adapter: str = "fake", config: dict[str, str] | None = None) -> dict[str, Any]:
    """Connect an adapter and return a JSON-serializable, deterministically ordered result."""
    return asyncio.run(_connect(adapter, config or {}))


def _parse_config(pairs: list[str]) -> dict[str, str]:
    config: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise SystemExit(f"--config expects KEY=VALUE, got {pair!r}")
        config[key] = value
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--adapter", default="fake", help="registered adapter name")
    parser.add_argument("--config", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON here")
    args = parser.parse_args(argv)

    try:
        result = run(args.adapter, _parse_config(args.config))
    except AdapterNotFound as exc:
        raise SystemExit(str(exc))

    text = json.dumps(result, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
