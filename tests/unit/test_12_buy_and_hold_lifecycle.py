"""strategies/12_buy_and_hold_lifecycle: buy and hold lifecycle test."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Protocol, cast

import pytest

_PATH = (
    Path(__file__).resolve().parents[2] / "examples" / "strategies" / "12_buy_and_hold_lifecycle.py"
)


class ExampleModule(Protocol):
    def run(self, bars: int = ..., quantity: float = ...) -> dict[str, object]: ...
    def main(self, argv: list[str] | None = None) -> int: ...


@pytest.fixture(scope="module")
def example() -> ExampleModule:
    spec = importlib.util.spec_from_file_location("buy_and_hold_lifecycle", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(ExampleModule, module)


def test_run_returns_strategy_config_and_result(example: ExampleModule) -> None:
    result = example.run(bars=20)
    strat = cast(dict[str, object], result["strategy"])
    cfg = cast(dict[str, object], result["config"])
    assert strat["name"] == "buy_and_hold_lifecycle"
    assert cfg["quantity"] == 1.0


def test_fills_only_once(example: ExampleModule) -> None:
    result = example.run(bars=50)
    res = cast(dict[str, object], result["result"])
    fills = cast(list[dict[str, object]], res["fills"])
    assert len(fills) == 1
    assert fills[0]["side"] == "buy"
    assert fills[0]["quantity"] == 1.0


def test_main_happy_path_returns_zero(
    example: ExampleModule, capsys: pytest.CaptureFixture[str]
) -> None:
    assert example.main([]) == 0
    captured = capsys.readouterr().out
    assert "Strategy starting" in captured
    assert "Strategy stopping" in captured
