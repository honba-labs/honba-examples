"""ai_research/05_mcp_integration: load and report the static MCP tool schema."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "ai_research" / "05_mcp_integration.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("mcp_integration", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_loads_the_mcp_schema(example):
    result = example.run()
    assert set(result) == {"schema_path", "comment", "tool_count", "tools"}
    assert result["tool_count"] == 6
    assert result["comment"] is not None and "honba-codegen" in result["comment"]


def test_each_tool_has_name_description_readonly_and_schemas(example):
    result = example.run()
    for tool in result["tools"]:
        assert "name" in tool and tool["name"]
        assert "description" in tool and tool["description"]
        assert tool["read_only"] is True
        assert tool["input_schema"] is not None
        # The generated schema has inputSchema only; outputSchema is optional in MCP


def test_tool_names_match_the_six_design_tools(example):
    result = example.run()
    names = {t["name"] for t in result["tools"]}
    expected = {
        "backtest",
        "sweep",
        "verify_strategy",
        "screen",
        "get_instruments",
        "get_bars",
    }
    assert names == expected


def test_output_is_deterministic_across_two_calls(example):
    import json as _json
    first = _json.dumps(example.run(), sort_keys=True, default=str)
    second = _json.dumps(example.run(), sort_keys=True, default=str)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "mcp_tools.json"
    assert example.main(["--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    file_json = json.loads(out.read_text())
    stdout_json = json.loads(stdout)
    assert file_json == stdout_json
    assert file_json["tool_count"] == 6


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main([]) == 0
    assert "backtest" in capsys.readouterr().out