"""honba_examples.pipeline.io: stream parse/emit helpers.

Pure symbol/JSON stream helpers mirroring the CLI chaining contract used by
``backtesting/10_chained_screener_and_sip.py`` and the standalone screeners, so piped symbol
streams can be ingested into (and produced from) a pipeline.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from typing import Any, TextIO

__all__ = ["dumps", "parse_symbols", "read_symbols", "to_stream"]


def parse_symbols(raw: str) -> list[str]:
    """Parse a whitespace/comma symbol stream or JSON payload into upper-cased symbols.

    Accepts a list of strings, a list of objects carrying a ``symbol`` key, or a dict with a
    ``candidates`` / ``symbols`` list. Malformed JSON falls back to whitespace splitting.
    """
    text = (raw or "").strip()
    if not text:
        return []

    symbols: list[str] = []
    if text.startswith(("{", "[")):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, str):
                    symbols.append(item.strip().upper())
                elif isinstance(item, dict) and "symbol" in item:
                    symbols.append(str(item["symbol"]).strip().upper())
        elif isinstance(parsed, dict):
            if isinstance(parsed.get("candidates"), list):
                for c in parsed["candidates"]:
                    if isinstance(c, dict) and "symbol" in c:
                        symbols.append(str(c["symbol"]).strip().upper())
                    elif isinstance(c, str):
                        symbols.append(c.strip().upper())
            elif isinstance(parsed.get("symbols"), list):
                symbols = [str(s).strip().upper() for s in parsed["symbols"]]

    if not symbols:
        symbols = [s.strip().upper() for s in text.replace(",", " ").split() if s.strip()]
    return [s for s in symbols if s]


def read_symbols(stream: TextIO | None = None) -> list[str]:
    """Read a symbol stream from ``stream`` (default ``sys.stdin``); never blocks on a TTY."""
    stream = stream if stream is not None else sys.stdin
    if stream is None:
        return []
    try:
        if stream.isatty():
            return []
        raw = stream.read()
    except (OSError, ValueError):
        return []
    return parse_symbols(raw)


def dumps(payload: Any, *, indent: int | None = 2) -> str:
    """JSON-encode ``payload`` stringifying dates and other non-native objects."""
    return json.dumps(payload, indent=indent, default=str)


def to_stream(symbols: Iterable[str], fmt: str = "symbols") -> str:
    """Render ``symbols`` as a ``plain`` (newline), ``symbols`` (space) or ``json`` stream."""
    items = list(symbols)
    if fmt == "plain":
        return "\n".join(items)
    if fmt == "json":
        return dumps({"symbols": items})
    return " ".join(items)
