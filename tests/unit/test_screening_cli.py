"""tests/unit/test_screening_cli.py: Unit tests for standalone screener CLIs (Phase 1 stream contract)."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from honba_examples.screener_cli import ScreenerCLIExample

_ROOT = Path(__file__).resolve().parents[2]


def _load(module_name: str, relative: str):
    spec = importlib.util.spec_from_file_location(module_name, _ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def multibagger_script():
    return _load("screening_01_multibagger", "screening/01_multibagger_screener.py")


@pytest.fixture(scope="module")
def value_script():
    return _load("screening_02_value", "screening/02_value_52w_low_screener.py")


@pytest.fixture(scope="module")
def dividend_script():
    return _load("screening_03_dividend", "screening/03_dividend_yield_screener.py")


_CANDIDATES = [
    {"symbol": "AAA", "close": 100.0, "roce_pct": 18.0},
    {"symbol": "BBB", "close": 200.0, "roce_pct": None},
]
_ASOF = dt.date(2023, 1, 1)


def test_all_screeners_inherit_base(multibagger_script, value_script, dividend_script):
    assert issubclass(multibagger_script.MultibaggerScreenerExample, ScreenerCLIExample)
    assert issubclass(value_script.Value52wLowScreenerExample, ScreenerCLIExample)
    assert issubclass(dividend_script.DividendYieldScreenerExample, ScreenerCLIExample)


@pytest.mark.parametrize("stream_format", ["symbols", "plain", "json"])
def test_symbol_stream_is_pure(
    stream_format, multibagger_script, value_script, dividend_script, capsys
):
    examples = [
        multibagger_script.MultibaggerScreenerExample(),
        value_script.Value52wLowScreenerExample(),
        dividend_script.DividendYieldScreenerExample(),
    ]
    for example in examples:
        example.stream_format = stream_format
        example._emit(_CANDIDATES, _ASOF)
        out = capsys.readouterr().out
        if stream_format == "symbols":
            assert out.strip() == "AAA BBB"
        elif stream_format == "plain":
            assert out.strip() == "AAA\nBBB"
        else:
            payload = json.loads(out)
            assert [c["symbol"] for c in payload["candidates"]] == ["AAA", "BBB"]
            assert payload["asof_date"] == "2023-01-01"


def test_csv_stream(multibagger_script, capsys):
    example = multibagger_script.MultibaggerScreenerExample()
    example.stream_format = "csv"
    example._emit(_CANDIDATES, _ASOF)
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0].startswith("symbol,")
    assert lines[1].startswith("AAA,")
    assert lines[2].startswith("BBB,")


def test_diagnostics_go_to_stderr(multibagger_script, monkeypatch, capsys):
    example = multibagger_script.MultibaggerScreenerExample()
    example.stream_format = "symbols"
    monkeypatch.setattr(example, "asof_str", "2099-01-01")
    asof = example.resolve_asof(example.resolve_catalog_dir())
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "beyond the latest catalog date" in captured.err
    assert asof <= dt.date(2100, 1, 1)


def test_invalid_asof_raises_systemexit(multibagger_script):
    example = multibagger_script.MultibaggerScreenerExample()
    example.asof_str = "not-a-date"
    with pytest.raises(SystemExit):
        example.resolve_asof(example.resolve_catalog_dir())


def test_no_candidates_emits_empty_stream(multibagger_script, monkeypatch, capsys):
    example = multibagger_script.MultibaggerScreenerExample()
    example.stream_format = "symbols"
    monkeypatch.setattr(example, "screen", lambda catalog_dir, asof: [])
    code = example.run()
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""
    assert "No candidates found" in captured.err


def test_screener_stream_feeds_backtester_parser():
    """The plain/JSON screener stream must be parseable by the 08 backtester."""
    script = _load("screening_08_parser", "backtesting/08_chained_screener_and_sip.py")
    parse = script.ChainedScreenerSipExample._parse_symbols_stream
    assert parse("AAA\nBBB\n") == ["AAA", "BBB"]
    assert parse('{"candidates": [{"symbol": "AAA"}, {"symbol": "BBB"}]}') == ["AAA", "BBB"]
