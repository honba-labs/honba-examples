"""strategies/15_multi_symbol_portfolio: 5-scrip portfolio unit tests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

_PATH = (
    Path(__file__).resolve().parents[2] / "examples" / "strategies" / "15_multi_symbol_portfolio.py"
)


class ExampleModule(Protocol):
    def run(
        self,
        bars: int = ...,
        fast: int = ...,
        slow: int = ...,
        rebalance_every: int = ...,
        max_weight: float = ...,
        symbol_max_loss_pct: float = ...,
        symbols: tuple[str, ...] = ...,
    ) -> dict[str, object]: ...

    def main(self, argv: list[str] | None = None) -> int: ...


@pytest.fixture(scope="module")
def example() -> ExampleModule:
    spec = importlib.util.spec_from_file_location("multi_symbol_portfolio", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["multi_symbol_portfolio"] = module
    spec.loader.exec_module(module)
    return cast(ExampleModule, cast(object, module))


def test_run_returns_expected_structure(example: ExampleModule) -> None:
    result = example.run(bars=50)
    assert set(result.keys()) == {"strategy", "config", "result"}
    strat = cast(dict[str, object], result["strategy"])
    cfg = cast(dict[str, object], result["config"])
    assert strat["name"] == "multi_symbol_portfolio"
    symbols = cast(tuple[str, ...], cfg["symbols"])
    assert len(symbols) == 5
    assert cfg["max_weight"] == 0.20


def test_trades_across_all_five_symbols(example: ExampleModule) -> None:
    result = example.run(bars=80)
    res = cast(dict[str, object], result["result"])
    fills = cast(list[dict[str, object]], res["fills"])
    symbols_traded: set[str] = set()
    for f in fills:
        iid = cast(dict[str, object], f["instrument_id"])
        symbols_traded.add(str(iid["symbol"]))
    expected = {"RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"}
    assert symbols_traded == expected


def test_per_symbol_weight_cap_enforced(example: ExampleModule) -> None:
    # Run with max_weight = 0.15 (15%)
    result = example.run(bars=60, max_weight=0.15)
    res = cast(dict[str, object], result["result"])
    fills = cast(list[dict[str, object]], res["fills"])
    for f in fills:
        if str(f.get("side")) == "buy":
            # Initial capital is 100k, 15% is 15,000 INR
            qty = float(str(f["quantity"]))
            px = float(str(f["price"]))
            notional = qty * px
            assert notional <= 16_000.0  # within margin of integer lot rounding


def test_main_happy_path_returns_zero(
    example: ExampleModule, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out: Path = tmp_path / "multi_symbol.json"
    assert example.main(["--out", str(out)]) == 0
    stdout: str = capsys.readouterr().out
    file_json = cast(dict[str, object], json.loads(out.read_text()))
    strat = cast(dict[str, object], file_json["strategy"])
    assert strat["name"] == "multi_symbol_portfolio"
    assert "multi_symbol_portfolio" in stdout
