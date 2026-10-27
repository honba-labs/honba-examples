"""tests/unit/test_12_ai_drawdown_refinement: AI drawdown refinement."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "ai_research"
    / "12_ai_drawdown_refinement.py"
)


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("ai_drawdown_refinement", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_shape(example) -> None:
    result = example.run(bars=200, seed=42)
    assert set(result) >= {
        "config",
        "diagnosis",
        "base",
        "enhanced_adx_filter",
        "refinement",
    }
    assert result["config"]["symbol"] == "RELIANCE"
    assert result["config"]["seed"] == 42
    diag = result["diagnosis"]
    assert set(diag) >= {
        "drawdown_triggered",
        "in_sample_max_drawdown_pct",
        "ai_diagnosis",
        "guard_passed",
        "guard_reasons",
    }
    assert isinstance(diag["drawdown_triggered"], bool)
    assert isinstance(diag["guard_passed"], bool)


def test_refinement_fields(example) -> None:
    result = example.run(bars=200, seed=42)
    ref = result["refinement"]
    assert set(ref) >= {
        "oos_is_ratio",
        "dsr",
        "dsr_trials",
        "sharpe_improved",
        "drawdown_reduced",
    }
    assert ref["dsr_trials"] > 0
    assert isinstance(ref["sharpe_improved"], bool)
    assert isinstance(ref["drawdown_reduced"], bool)


def test_base_and_enhanced_have_out_of_sample(example) -> None:
    result = example.run(bars=200, seed=42)
    for key in ("base", "enhanced_adx_filter"):
        oos = result[key]["out_of_sample"]
        assert set(oos) >= {"sharpe", "max_drawdown_pct", "final_equity", "n_fills"}
        assert oos["final_equity"] > 0


def test_deterministic_across_two_runs(example) -> None:
    first = json.dumps(example.run(seed=7), sort_keys=True, default=str)
    second = json.dumps(example.run(seed=7), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys) -> None:
    out = tmp_path / "refinement.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    assert json.loads(out.read_text()) == json.loads(stdout)
    assert json.loads(stdout)["diagnosis"]["ai_diagnosis"]


def test_main_happy_path_returns_zero(example, capsys) -> None:
    assert example.main([]) == 0
    out = capsys.readouterr().out
    assert "ADX" in out or "drawdown" in out.lower() or "AI" in out
