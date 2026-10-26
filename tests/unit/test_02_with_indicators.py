"""strategies/02_with_indicators: composite SMA + RSI + ATR on synthetic bars."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Protocol, cast

import pytest

_PATH = Path(__file__).resolve().parents[2] / "examples" / "strategies" / "02_with_indicators.py"


class ExampleModule(Protocol):
    def run(
        self,
        bars: int = ...,
        fast: int = ...,
        slow: int = ...,
        rsi_period: int = ...,
        atr_period: int = ...,
        risk_pct: float = ...,
        use_ema: bool = ...,
    ) -> dict[str, object]: ...

    def main(self, argv: list[str] | None = None) -> int: ...


@pytest.fixture(scope="module")
def example() -> ExampleModule:
    spec = importlib.util.spec_from_file_location("with_indicators", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(ExampleModule, cast(object, module))


def test_run_returns_strategy_config_and_result(example: ExampleModule) -> None:
    result = example.run(bars=20)
    assert set(result.keys()) == {"strategy", "config", "result"}
    strat = cast(dict[str, object], result["strategy"])
    cfg = cast(dict[str, object], result["config"])
    assert strat["name"] == "composite_sma_rsi"
    assert cfg["fast"] == 10
    assert cfg["slow"] == 30
    assert cfg["rsi_period"] == 14
    assert cfg["atr_period"] == 14
    assert cfg["use_ema"] is False


def test_result_contains_fills_and_intents(example: ExampleModule) -> None:
    result = example.run(bars=100)
    res = cast(dict[str, object], result["result"])
    assert "fills" in res
    assert "intents" in res


def test_deterministic_across_two_runs(example: ExampleModule) -> None:
    first = json.dumps(example.run(bars=50), sort_keys=True, default=str)
    second = json.dumps(example.run(bars=50), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(
    example: ExampleModule, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out: Path = tmp_path / "with_indicators.json"
    assert example.main(["--out", str(out)]) == 0
    stdout: str = capsys.readouterr().out
    file_json = cast(dict[str, object], json.loads(out.read_text()))
    stdout_json = cast(dict[str, object], json.loads(stdout))
    assert file_json == stdout_json
    strat = cast(dict[str, object], file_json["strategy"])
    assert strat["name"] == "composite_sma_rsi"


def test_main_happy_path_returns_zero(
    example: ExampleModule, capsys: pytest.CaptureFixture[str]
) -> None:
    assert example.main([]) == 0
    assert "composite_sma_rsi" in capsys.readouterr().out


def test_default_run_trades_both_sides(example: ExampleModule) -> None:
    """The default series oscillates, so RSI leaves 100 and entries get through."""
    result = example.run()
    res = cast(dict[str, object], result["result"])
    fills = cast(list[dict[str, object]], res["fills"])
    intents = cast(list[object], res["intents"])
    sides: set[str] = {str(f.get("side")) for f in fills}
    assert len(intents) >= 2
    assert sides == {"buy", "sell"}


def test_ema_variant_runs_successfully(example: ExampleModule) -> None:
    """Running with use_ema=True enables the EMA trend filter."""
    result = example.run(bars=100, use_ema=True)
    cfg = cast(dict[str, object], result["config"])
    assert cfg["use_ema"] is True
    res = cast(dict[str, object], result["result"])
    assert "fills" in res
    assert "intents" in res
    fills = cast(list[object], res["fills"])
    assert len(fills) >= 1
