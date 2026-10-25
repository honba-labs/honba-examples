"""strategies/13_bracket_order_mis: bracket order and MIS auto-square-off unit tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Protocol, cast

import pytest

_PATH = Path(__file__).resolve().parents[2] / "examples" / "strategies" / "13_bracket_order_mis.py"


class ExampleModule(Protocol):
    def run(
        self,
        bars: int = ...,
        quantity: float = ...,
        stop_loss_pct: float = ...,
        take_profit_pct: float = ...,
        margin_multiplier: float = ...,
        capital: float = ...,
        price_path: list[float] | None = ...,
    ) -> dict[str, object]: ...

    def main(self, argv: list[str] | None = None) -> int: ...


@pytest.fixture(scope="module")
def example() -> ExampleModule:
    spec = importlib.util.spec_from_file_location("bracket_order_mis", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(ExampleModule, module)


def test_run_returns_expected_structure(example: ExampleModule) -> None:
    result = example.run(bars=13)
    assert set(result.keys()) == {"strategy", "config", "result"}
    strat = cast(dict[str, object], result["strategy"])
    cfg = cast(dict[str, object], result["config"])
    assert strat["name"] == "bracket_order_mis"
    assert cfg["quantity"] == 10.0
    assert cfg["margin_multiplier"] == 5.0


def test_mis_auto_square_off_at_1520(example: ExampleModule) -> None:
    """If neither TP nor SL is touched, the 15:20 bar forces a market square-off."""
    flat_path = [2500.0] * 13
    result = example.run(bars=13, price_path=flat_path)
    res = cast(dict[str, object], result["result"])
    fills = cast(list[dict[str, object]], res["fills"])
    assert len(fills) == 2
    assert fills[0]["side"] == "buy"
    assert fills[1]["side"] == "sell"
    assert fills[0]["quantity"] == fills[1]["quantity"]


def test_take_profit_bracket_trigger(example: ExampleModule) -> None:
    """When price rises beyond take_profit_pct, take-profit exit fires before 15:20."""
    jump_path = [2500.0, 2500.0, 2500.0, 2650.0] + [2650.0] * 9
    result = example.run(bars=13, take_profit_pct=0.04, price_path=jump_path)
    res = cast(dict[str, object], result["result"])
    fills = cast(list[dict[str, object]], res["fills"])
    assert len(fills) == 2
    assert fills[0]["side"] == "buy"
    assert fills[1]["side"] == "sell"
    assert float(str(fills[1]["price"])) >= 2600.0


def test_stop_loss_bracket_trigger(example: ExampleModule) -> None:
    """When price drops beyond stop_loss_pct, stop-loss exit fires before 15:20."""
    drop_path = [2500.0, 2500.0, 2420.0] + [2420.0] * 10
    result = example.run(bars=13, stop_loss_pct=0.02, price_path=drop_path)
    res = cast(dict[str, object], result["result"])
    fills = cast(list[dict[str, object]], res["fills"])
    assert len(fills) == 2
    assert fills[0]["side"] == "buy"
    assert fills[1]["side"] == "sell"
    assert float(str(fills[1]["price"])) <= 2450.0


def test_main_happy_path_returns_zero(
    example: ExampleModule, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out: Path = tmp_path / "bracket_mis.json"
    assert example.main(["--out", str(out)]) == 0
    stdout: str = capsys.readouterr().out
    file_json = cast(dict[str, object], json.loads(out.read_text()))
    strat = cast(dict[str, object], file_json["strategy"])
    assert strat["name"] == "bracket_order_mis"
    assert "bracket_order_mis" in stdout
