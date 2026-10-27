"""tests/unit/test_10_claude_autonomous_quant: MCP quantitative agent workflow."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "ai_research"
    / "10_claude_autonomous_quant.py"
)


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("claude_autonomous_quant", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_shape(example) -> None:
    result = example.run(underlying="RELIANCE", bars=120, seed=42)
    assert set(result) >= {
        "config",
        "mcp_tools",
        "hypothesis",
        "backtest",
        "dsr",
        "agent_decision",
    }
    assert result["config"]["underlying"] == "RELIANCE"
    assert result["config"]["llm"] == "fake"
    assert result["config"]["bars"] == 120
    assert result["mcp_tools"]  # at least one capability enumerated
    hyp = result["hypothesis"]
    assert set(hyp) >= {"name", "rationale", "entry_rule", "exit_rule", "params"}
    assert hyp["name"] == "rsi_mean_reversion"


def test_backtest_and_dsr_fields(example) -> None:
    result = example.run(underlying="RELIANCE", bars=120, seed=42)
    bt = result["backtest"]
    assert set(bt) >= {"n_fills", "observed_sharpe", "final_equity", "skew", "kurtosis"}
    assert isinstance(bt["observed_sharpe"], float)
    assert bt["final_equity"] > 0
    dsr = result["dsr"]
    assert set(dsr) >= {"value", "n_trials", "trial_sharpe_mean", "decision"}
    assert dsr["decision"] in ("approve", "veto")
    assert dsr["n_trials"] > 0


def test_agent_decision_record(example) -> None:
    result = example.run(underlying="RELIANCE", bars=120, seed=42)
    dec = result["agent_decision"]
    assert dec["decision"] in ("approve", "veto")
    assert dec["original_qty"] == 100.0
    assert dec["decided_qty"] in (0.0, 100.0)
    assert isinstance(dec["rationale"], str) and dec["rationale"]


def test_deterministic_across_two_runs(example) -> None:
    first = json.dumps(example.run(seed=11), sort_keys=True, default=str)
    second = json.dumps(example.run(seed=11), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys) -> None:
    out = tmp_path / "quant.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    assert json.loads(out.read_text()) == json.loads(stdout)
    assert json.loads(stdout)["config"]["llm"] == "fake"


def test_main_happy_path_returns_zero(example, capsys) -> None:
    assert example.main([]) == 0
    assert "rsi_mean_reversion" in capsys.readouterr().out
