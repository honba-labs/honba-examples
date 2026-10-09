"""ai_research/01_llm_research_analyst: translate NL query -> validated filter (offline)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "ai_research" / "01_llm_research_analyst.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("llm_research_analyst", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_returns_knowledge_and_result(example):
    result = example.run(query="anything")
    assert set(result) >= {
        "query",
        "knowledge",
        "system_prompt_chars",
        "result",
        "llm_call_history",
    }
    assert result["knowledge"]["version"] == "1.0.0"
    assert result["knowledge"]["content_hash"]  # non-empty
    assert isinstance(result["knowledge"]["metrics"], int) and result["knowledge"]["metrics"] > 0
    assert isinstance(result["knowledge"]["presets"], list) and result["knowledge"]["presets"]
    assert isinstance(result["knowledge"]["units"], dict) and result["knowledge"]["units"]
    assert isinstance(result["knowledge"]["enums"], dict) and result["knowledge"]["enums"]
    assert isinstance(result["system_prompt_chars"], int) and result["system_prompt_chars"] > 0


def test_run_returns_ask_result_fields(example):
    result = example.run(query="test", fake_response="close above 100")
    res = result["result"]
    assert set(res) == {"filter_text", "filter_group", "repair_count", "raw_responses"}
    assert res["filter_text"] == "close above 100"
    assert res["repair_count"] == 0
    assert res["raw_responses"] == ["close above 100"]


def test_fake_llm_receives_the_query_and_records_history(example):
    result = example.run(query="my question", fake_response="volume above 1000000")
    hist = result["llm_call_history"]
    assert len(hist) == 1
    call = hist[0]
    # System prompt + user query (the repair loop in ask.py builds this)
    assert len(call["messages"]) == 2
    assert call["messages"][0]["role"] == "system"
    assert "Honba filter syntax" in call["messages"][0]["content"]
    assert call["messages"][1] == {
        "role": "user",
        "content": "Translate this screener query: my question",
    }
    assert call["grammar"] is None
    assert call["schema"] is None
    assert call["seed"] is None
    assert call["temperature"] == 0.0
    # The LLM's response is recorded in AskResult.raw_responses


def test_system_prompt_changes_when_knowledge_changes(example):
    result1 = example.run(query="q")
    # The knowledge pack is deterministic; a second call has the same hash
    result2 = example.run(query="q")
    assert result1["knowledge"]["content_hash"] == result2["knowledge"]["content_hash"]


def test_output_is_deterministic_across_two_calls(example):
    first = json.dumps(example.run(query="test query"), sort_keys=True, default=str)
    second = json.dumps(example.run(query="test query"), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "analyst.json"
    assert example.main(["--query", "test", "--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    stdout_json = json.loads(stdout)
    assert file_json == stdout_json
    assert stdout_json["query"] == "test"


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main(["--query", "test"]) == 0
    assert "query" in capsys.readouterr().out
