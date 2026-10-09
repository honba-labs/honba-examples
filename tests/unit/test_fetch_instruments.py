"""basic/02_fetch_instruments: offline against the deterministic FakeAdapter."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "basic" / "02_fetch_instruments.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("fetch_instruments", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fetch_returns_sorted_instruments(example):
    result = example.run(adapter="fake")
    symbols = [row["symbol"] for row in result["instruments"]]
    assert symbols and symbols == sorted(symbols)
    assert result["adapter"] == "fake"
    assert result["count"] == len(symbols)


def test_search_narrows_the_list(example):
    full = example.run(adapter="fake")
    hit = example.run(adapter="fake", query="RELIANCE")
    assert 0 < hit["count"] < full["count"]
    assert all("RELIANCE" in row["symbol"] for row in hit["instruments"])


def test_rows_carry_lot_and_tick_metadata(example):
    row = example.run(adapter="fake")["instruments"][0]
    assert set(row) >= {"symbol", "exchange", "kind", "lot_size", "tick_size", "currency"}
    assert row["lot_size"] > 0 and row["tick_size"] > 0


def test_summary_counts_by_kind(example):
    result = example.run(adapter="fake")
    assert sum(result["by_kind"].values()) == result["count"]


def test_output_is_deterministic_json(example):
    a = json.dumps(example.run(adapter="fake"), sort_keys=True)
    b = json.dumps(example.run(adapter="fake"), sort_keys=True)
    assert a == b


def test_unknown_adapter_raises_with_alternatives(example):
    with pytest.raises(Exception, match="fake"):
        example.run(adapter="no-such-broker")


def test_main_writes_json(example, tmp_path, capsys):
    out = tmp_path / "instruments.json"
    assert example.main(["--adapter", "fake", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["adapter"] == "fake"
    assert "instruments" in capsys.readouterr().out
