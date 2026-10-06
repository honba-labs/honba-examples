"""basic/01_connect_dhan: connect an adapter through the registry (default fake, offline)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from honba.adapters.errors import AdapterNotFound

_PATH = Path(__file__).resolve().parents[2] / "basic" / "01_connect_dhan.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("connect_dhan", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_connect_reports_connected_session(example):
    result = example.run(adapter="fake")
    assert result["connected"] is True
    assert result["adapter"] == "fake"
    assert result["session"]["user_id"] == "FAKE_USER"
    assert result["session"]["mode"] == "live"
    assert result["session"]["accounts"] == []
    assert result["session"]["expires_at"] is None


def test_capabilities_are_json_with_enum_values(example):
    caps = example.run(adapter="fake")["capabilities"]
    assert caps["name"] == "fake"
    assert caps["exchanges"] == ["NSE"]
    assert caps["order_types"] == ["limit", "market"]
    assert all(isinstance(item, str) for item in caps["products"])


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "connection.json"
    assert example.main(["--adapter", "fake", "--out", str(out)]) == 0
    stdout_json = json.loads(capsys.readouterr().out)
    assert json.loads(out.read_text()) == stdout_json
    assert stdout_json["connected"] is True


def test_output_is_deterministic_across_two_calls(example):
    first = json.dumps(example.run(adapter="fake"), sort_keys=True)
    second = json.dumps(example.run(adapter="fake"), sort_keys=True)
    assert first == second


def test_unknown_adapter_names_the_installed_alternatives(example):
    with pytest.raises(AdapterNotFound, match="no-such-broker") as excinfo:
        example.run(adapter="no-such-broker")
    assert "fake" in str(excinfo.value)


def test_main_reports_a_missing_adapter_without_a_traceback(example, capsys):
    with pytest.raises(SystemExit) as excinfo:
        example.main(["--adapter", "no-such-broker"])
    assert "no-such-broker" in str(excinfo.value)
    assert "Traceback" not in capsys.readouterr().err


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main(["--adapter", "fake"]) == 0
    assert "connected" in capsys.readouterr().out
