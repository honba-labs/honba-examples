"""Honba domain values as deterministic, JSON-serialisable built-ins.

Broker adapters and the engine hand back enums, frozensets, tuples and frozen
dataclasses (``SessionInfo``, ``QuoteTick``, ``OrderReport``, ``Position``, ...).
``jsonable`` turns them into the plain ``dict``/``list``/``str``/``int``/``float``/``bool``
image an example prints: enums by their value, unordered sets sorted, ordered
sequences and dataclass fields kept in their declared order, dates ISO-8601.
Two runs over the same values therefore produce the same bytes.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from enum import Enum
from pathlib import Path
from typing import Any

__all__ = ["jsonable"]


def jsonable(value: Any) -> Any:
    """Plain JSON image of ``value``; see the module docstring for the mapping."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (set, frozenset)):
        return sorted((jsonable(item) for item in value), key=repr)
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        # Recurse per value: dataclasses.asdict would pre-convert nested values and
        # hide their types (a nested date would reach the str() fallback as repr).
        return {str(key): jsonable(item) for key, item in value.items()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)
        }
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
