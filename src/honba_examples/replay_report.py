"""Replay a strategy on bars and package the JSON report the strategy examples print."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from honba.strategies.base import Strategy
from honba.strategies.testing import replay

from honba_examples.jsonable import jsonable

__all__ = ["replay_report"]


def replay_report(
    strategy: Strategy,
    bars: Sequence[Any],
    params: Mapping[str, Any],
    *,
    config_extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run ``strategy`` over ``bars`` with ``honba.strategies.testing.replay``.

    Returns ``{"strategy": {name, warmup_bars, params}, "config": {...}, "result": ...}``.
    ``config`` is ``params`` followed by ``config_extra`` (e.g. the bar count).
    """
    return {
        "strategy": {
            "name": strategy.name,
            "warmup_bars": strategy.warmup_bars,
            "params": dict(params),
        },
        "config": {**params, **(config_extra or {})},
        "result": jsonable(replay(strategy, list(bars))),
    }
