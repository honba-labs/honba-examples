"""tests/unit/test_04_market_maker: Avellaneda-Stoikov quoting with OBI filter."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "examples" / "stocksharp" / "04_market_maker.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("market_maker", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_summary_keys(example) -> None:
    result = example.run(steps=240, seed=42)
    assert set(result) == {"config", "quotes", "fills", "summary"}
    summary = result["summary"]
    assert summary["quote_steps"] == 240
    assert 0 <= summary["paused_steps"] <= 240
    assert summary["fill_count"] >= 0
    assert isinstance(summary["mark_to_market_pnl"], float)


def test_quotes_have_expected_shape(example) -> None:
    result = example.run(steps=60, seed=42, obi_limit=0.0)  # pause everything
    assert all(q["paused"] is True for q in result["quotes"])
    active = example.run(steps=60, seed=42, obi_limit=2.0)  # never pause
    assert all(q["paused"] is False for q in active["quotes"])
    active_quote = next(q for q in active["quotes"] if not q["paused"])
    assert set(active_quote) >= {"step", "mid", "bid", "ask", "spread", "inventory", "obi"}
    assert active_quote["ask"] > active_quote["bid"]


def test_inventory_limited(example) -> None:
    result = example.run(steps=240, seed=42, inventory_limit=50)
    assert abs(result["summary"]["final_inventory"]) <= 50


def test_deterministic_across_two_runs(example) -> None:
    first = json.dumps(example.run(seed=12), sort_keys=True, default=str)
    second = json.dumps(example.run(seed=12), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys) -> None:
    out = tmp_path / "mm.json"
    assert example.main(["--out", str(out)]) == 0
    assert json.loads(out.read_text()) == json.loads(capsys.readouterr().out)


def test_main_happy_path_returns_zero(example) -> None:
    assert example.main([]) == 0
