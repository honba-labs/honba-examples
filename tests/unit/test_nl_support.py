"""tests/unit/test_nl_support.py: unit tests for the natural-language screener helpers."""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from honba_examples.screening.cli_args import add_nl_screener_args
from honba_examples.screening.nl_support import (
    MCAP_UNIT_MULTIPLIER,
    VOLUME_UNIT_MULTIPLIER,
    basket_statistics,
    build_point_in_time_row,
    forward_performance,
    resolve_asof,
    resolve_llm_provider,
    safe_eval_predicate,
)

TODAY = dt.date(2026, 10, 13)
_PATH = Path(__file__).resolve().parents[2] / "ai_research" / "03_nl_screener.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("nl_screener", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolve_asof_explicit_and_default() -> None:
    assert resolve_asof("x", "2021-10-11", None, False, TODAY) == (dt.date(2021, 10, 11), False)
    assert resolve_asof("x", None, None, False, TODAY) == (TODAY, False)


def test_resolve_asof_lookback_and_query_phrases() -> None:
    assert resolve_asof("x", None, 3, False, TODAY) == (dt.date(2023, 10, 13), False)
    assert resolve_asof("rsi < 30 5 years ago", None, None, False, TODAY) == (
        dt.date(2021, 10, 13),
        True,
    )
    assert resolve_asof("buy in 2019", None, None, False, TODAY) == (dt.date(2019, 10, 11), True)


def test_resolve_asof_rejects_bad_date() -> None:
    with pytest.raises(ValueError):
        resolve_asof("x", "notadate", None, False, TODAY)


def test_forward_performance_badges() -> None:
    d0, d1 = dt.date(2016, 1, 1), dt.date(2026, 1, 1)
    perf = forward_performance(10.0, 100.0, d0, d1)
    assert perf["multiplier"] == 10.0
    assert perf["return_pct"] == 900.0
    assert perf["badge"].startswith("10-Bagger")
    assert forward_performance(10.0, 50.0, d0, d1)["badge"].startswith("5-Bagger")
    assert forward_performance(10.0, 20.0, d0, d1)["badge"].startswith("Multibagger")
    assert forward_performance(10.0, 11.0, d0, d1)["badge"].startswith("Profitable")
    assert forward_performance(10.0, 9.0, d0, d1)["badge"].startswith("Loss")


def test_basket_statistics() -> None:
    assert basket_statistics([], TODAY, TODAY) == {}
    rows = [
        {"symbol": "A.NSE", "return_pct": 100.0, "multiplier": 2.0, "years_held": 1.0},
        {"symbol": "B.NSE", "return_pct": -50.0, "multiplier": 0.5, "years_held": 1.0},
    ]
    stats = basket_statistics(rows, dt.date(2025, 10, 13), TODAY)
    assert stats["basket_return_pct"] == 25.0
    assert stats["win_rate_pct"] == 50.0
    assert stats["multibagger_count"] == "1 / 2"
    assert stats["top_performer"] == "A.NSE (+100.0%, 2.00x)"


def test_build_point_in_time_row_needs_history() -> None:
    bars = [
        SimpleNamespace(ts=i, close=1.0, high=1.0, low=1.0, open=1.0, volume=1.0) for i in range(10)
    ]
    assert build_point_in_time_row("TCS", bars, 100, ["close"]) is None


def test_unit_multipliers() -> None:
    assert MCAP_UNIT_MULTIPLIER["cr"] == 1e7
    assert MCAP_UNIT_MULTIPLIER["lakh"] == 1e5
    assert VOLUME_UNIT_MULTIPLIER["k"] == 1_000


def test_safe_eval_predicate() -> None:
    code = "def match(row):\n    return row.get('volume', 0) > 5"
    assert safe_eval_predicate(code, {"volume": 10}) is True
    assert safe_eval_predicate(code, {}) is False


def test_resolve_llm_provider_fake_and_offline() -> None:
    sentinel = object()
    assert resolve_llm_provider("fake", lambda: sentinel).__class__.__name__ == "ScriptedFakeLlm"  # type: ignore[arg-type,return-value]
    assert resolve_llm_provider("ollama", lambda: sentinel).__class__.__name__ == "OllamaLlm"  # type: ignore[arg-type,return-value]


def test_nl_args_defaults() -> None:
    parser = argparse.ArgumentParser()
    add_nl_screener_args(parser)
    args = parser.parse_args([])
    assert (args.backend, args.format, args.limit, args.asof) == ("auto", "auto", 20, None)
    assert args.backtest is False


def test_script_translator_and_predicate(script) -> None:
    llm = script.RuleBasedLlamaTranslator()
    text = llm.complete(
        [{"role": "user", "content": "market_cap > 25000Cr and close above 50-day"}]
    )
    assert text == "market_cap > 250000000000 AND close > sma50"
    code = script.generate_python_predicate("q", text)
    assert script.safe_eval_predicate(code, {"market_cap": 3e11, "close": 2, "sma50": 1})
    assert not script.safe_eval_predicate(code, {"market_cap": 1e9, "close": 2, "sma50": 1})
    assert script.generate_python_predicate("q", "") == "def match(row):\n    return True"
