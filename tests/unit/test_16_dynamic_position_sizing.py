"""strategies/16_dynamic_position_sizing: dynamic position sizing unit tests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "strategies"
    / "16_dynamic_position_sizing.py"
)


class ExampleModule(Protocol):
    def run(
        self,
        bars: int = ...,
        mode: str = ...,
        fixed_quantity: float = ...,
        risk_pct: float = ...,
        atr_period: int = ...,
        atr_stop_mult: float = ...,
        capital: float = ...,
        compare: bool = ...,
        use_synthetic: bool = ...,
    ) -> dict[str, object]: ...

    def main(self, argv: list[str] | None = None) -> int: ...


@pytest.fixture(scope="module")
def example() -> ExampleModule:
    spec = importlib.util.spec_from_file_location("dynamic_position_sizing", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["dynamic_position_sizing"] = module
    spec.loader.exec_module(module)
    return cast(ExampleModule, module)


def test_run_returns_expected_structure(example: ExampleModule) -> None:
    result = example.run(bars=50, mode="fixed_fractional")
    assert set(result.keys()) == {"strategy", "config", "result"}
    strat = cast(dict[str, object], result["strategy"])
    cfg = cast(dict[str, object], result["config"])
    res = cast(dict[str, object], result["result"])
    assert strat["name"] == "dynamic_position_sizing"
    assert cfg["mode"] == "fixed_fractional"
    assert "fills" in res
    assert "sizes_history" in res


def test_fixed_lot_mode(example: ExampleModule) -> None:
    result = example.run(bars=100, mode="fixed_lot", fixed_quantity=15.0)
    res = cast(dict[str, object], result["result"])
    fills = cast(list[dict[str, object]], res["fills"])
    buy_fills: list[dict[str, object]] = [f for f in fills if str(f.get("side")) == "buy"]
    assert len(buy_fills) >= 1
    for f in buy_fills:
        assert float(str(f["quantity"])) == 15.0


def test_fixed_fractional_mode(example: ExampleModule) -> None:
    result = example.run(bars=100, mode="fixed_fractional", risk_pct=0.01)
    res = cast(dict[str, object], result["result"])
    fills = cast(list[dict[str, object]], res["fills"])
    buy_fills: list[dict[str, object]] = [f for f in fills if str(f.get("side")) == "buy"]
    assert len(buy_fills) >= 1
    # Risk 1% of 100k = 1000 INR; 2% stop on ~2500 price = 50 INR stop -> ~20 shares
    qty = float(str(buy_fills[0]["quantity"]))
    assert 10 <= qty <= 30


def test_volatility_targeted_mode(example: ExampleModule) -> None:
    result = example.run(bars=100, mode="volatility_targeted", risk_pct=0.01)
    res = cast(dict[str, object], result["result"])
    fills = cast(list[dict[str, object]], res["fills"])
    buy_fills: list[dict[str, object]] = [f for f in fills if str(f.get("side")) == "buy"]
    assert len(buy_fills) >= 1
    assert float(str(buy_fills[0]["quantity"])) >= 1


def test_compare_mode(example: ExampleModule) -> None:
    result = example.run(bars=60, compare=True)
    strat = cast(dict[str, object], result["strategy"])
    assert strat["name"] == "dynamic_position_sizing"
    res = cast(dict[str, dict[str, object]], result["result"])
    assert "fixed_lot" in res
    assert "fixed_fractional" in res
    assert "volatility_targeted" in res
    for mode_data in res.values():
        assert "avg_position_size" in mode_data
        assert "trade_count" in mode_data


def test_main_happy_path_returns_zero(
    example: ExampleModule, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out: Path = tmp_path / "sizing.json"
    assert example.main(["--out", str(out)]) == 0
    stdout: str = capsys.readouterr().out
    file_json = cast(dict[str, object], json.loads(out.read_text()))
    strat = cast(dict[str, object], file_json["strategy"])
    assert strat["name"] == "dynamic_position_sizing"
    assert "dynamic_position_sizing" in stdout
