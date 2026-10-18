"""tests/unit/test_screening_cli.py: Unit tests for standalone screener CLIs (Phase 1 stream contract)."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from honba_examples.screener_cli import ScreenerCLIExample

_ROOT = Path(__file__).resolve().parents[2] / "examples"


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


@pytest.fixture(scope="module")
def graham_script():
    return _load(
        "screening_04_intelligent_investor", "screening/04_intelligent_investor_screener.py"
    )


@pytest.fixture(scope="module")
def net_net_script():
    return _load("screening_05_net_net", "screening/05_net_net_screener.py")


@pytest.fixture(scope="module")
def mf_script():
    return _load("screening_06_mutual_fund", "screening/07_mutual_fund_screener.py")


_CANDIDATES = [
    {"symbol": "AAA", "close": 100.0, "roce_pct": 18.0},
    {"symbol": "BBB", "close": 200.0, "roce_pct": None},
]
_ASOF = dt.date(2023, 1, 1)


def test_all_screeners_inherit_base(
    multibagger_script, value_script, dividend_script, graham_script, net_net_script, mf_script
):
    assert issubclass(multibagger_script.MultibaggerScreenerExample, ScreenerCLIExample)
    assert issubclass(value_script.Value52wLowScreenerExample, ScreenerCLIExample)
    assert issubclass(dividend_script.DividendYieldScreenerExample, ScreenerCLIExample)
    assert issubclass(graham_script.IntelligentInvestorScreenerExample, ScreenerCLIExample)
    assert issubclass(net_net_script.NetNetScreenerExample, ScreenerCLIExample)
    assert issubclass(mf_script.MutualFundScreenerExample, ScreenerCLIExample)


@pytest.mark.parametrize("stream_format", ["symbols", "plain", "json"])
def test_symbol_stream_is_pure(
    stream_format,
    multibagger_script,
    value_script,
    dividend_script,
    graham_script,
    net_net_script,
    mf_script,
    capsys,
):
    examples = [
        multibagger_script.MultibaggerScreenerExample(),
        value_script.Value52wLowScreenerExample(),
        dividend_script.DividendYieldScreenerExample(),
        graham_script.IntelligentInvestorScreenerExample(),
        net_net_script.NetNetScreenerExample(),
        mf_script.MutualFundScreenerExample(),
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
    script = _load("screening_08_parser", "backtesting/10_chained_screener_and_sip.py")
    parse = script.ChainedScreenerSipExample._parse_symbols_stream

    # Space-separated
    assert parse("AAA BBB CCC") == ["AAA", "BBB", "CCC"]
    # Newline-separated
    assert parse("AAA\nBBB\nCCC") == ["AAA", "BBB", "CCC"]
    # Comma-separated
    assert parse("AAA, BBB, CCC") == ["AAA", "BBB", "CCC"]
    # JSON payload from screener
    json_doc = json.dumps({"candidates": [{"symbol": "AAA"}, {"symbol": "BBB"}]})
    assert parse(json_doc) == ["AAA", "BBB"]
    # Empty
    assert parse("") == []


def test_mutual_fund_screener_execution(mf_script, capsys):
    pytest.importorskip("honba_data", reason="mutual-fund screener needs the [mutual-funds] extra")
    example = mf_script.MutualFundScreenerExample()
    example.stream_format = "symbols"
    example.category = "Equity:Flexi Cap"
    catalog_dir = example.resolve_catalog_dir()
    asof = example.resolve_asof(catalog_dir)
    candidates = example.screen(catalog_dir, asof)
    assert len(candidates) >= 1
    codes = {c["scheme_code"] for c in candidates}
    assert "122639" in codes or "118989" in codes or "125354" in codes


def test_net_net_cli_defaults_to_graham_two_thirds(net_net_script):
    # default is Graham's 2/3 rule; 0 means rank-only
    assert net_net_script.NetNetScreenerExample.max_p_ncav == 0.67


def test_graham_cli_passes_only_explicit_thresholds(graham_script, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(
        graham_script, "screen_intelligent_investor", lambda **kw: seen.update(kw) or []
    )
    ex = graham_script.IntelligentInvestorScreenerExample()
    ex.screen(Path("."), _ASOF)
    assert seen["max_pe"] is None and seen["max_pb"] is None and seen["max_debt_equity"] is None
    err = capsys.readouterr().err
    assert "P/E <= 15" in err and "P/B <= 1.5" in err  # effective defensive thresholds printed
    ex.max_pe, ex.max_pb = 25.0, 0.0
    ex.screen(Path("."), _ASOF)
    assert seen["max_pe"] == 25.0 and seen["max_pb"] == 0.0
    assert "P/E <= 25" in capsys.readouterr().err


def test_all_screener_scripts_default_to_ninety_day_fiscal_lag(
    multibagger_script, value_script, dividend_script, graham_script, net_net_script
):
    for cls in (
        multibagger_script.MultibaggerScreenerExample,
        value_script.Value52wLowScreenerExample,
        dividend_script.DividendYieldScreenerExample,
        graham_script.IntelligentInvestorScreenerExample,
        net_net_script.NetNetScreenerExample,
    ):
        assert cls.fiscal_lag_days == 90, cls.__name__
