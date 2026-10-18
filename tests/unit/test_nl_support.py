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
_PATH = Path(__file__).resolve().parents[2] / "examples" / "ai_research" / "03_nl_screener.py"


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
    code = "def match(row):\n    return volume > 5"
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


# --- predicate generation: fail loudly instead of matching everything ------------------------


def test_empty_or_unparsed_filter_is_an_error(script) -> None:
    with pytest.raises(script.PredicateError, match="no clauses"):
        script.generate_python_predicate("q", "")
    with pytest.raises(script.PredicateError, match="no clauses"):
        script.generate_python_predicate("q", "   ")
    with pytest.raises(script.UnparsedClausesError) as exc:
        script.generate_python_predicate("q", "volume > 5 AND gibberish here AND close > 'x y'")
    assert "gibberish here" in str(exc.value)
    assert exc.value.clauses == ["gibberish here", "close > 'x y'"]


def test_unknown_metric_is_rejected_with_known_list(script) -> None:
    with pytest.raises(script.UnknownMetricError, match=r"unknown metric pe_ratio; known: .*close"):
        script.generate_python_predicate("q", "pe_ratio < 10")
    with pytest.raises(script.UnknownMetricError, match="unknown metric nonsense"):
        script.generate_python_predicate("q", "close > nonsense")


def test_translator_metric_without_row_support_is_rejected(script) -> None:
    # dividend yield is emitted by the rule-based translator but the row has no such metric
    text = script.RuleBasedLlamaTranslator().complete(
        [{"role": "user", "content": "dividend above 3"}]
    )
    with pytest.raises(script.UnknownMetricError, match="dividends_yield_current"):
        script.generate_python_predicate("q", text)


def test_missing_value_for_a_known_metric_is_false(script) -> None:
    code = script.generate_python_predicate("q", "rsi < 40")
    assert script.safe_eval_predicate(code, {"rsi": 30.0}) is True
    assert script.safe_eval_predicate(code, {}) is False
    assert script.safe_eval_predicate(code, {"rsi": None}) is False
    assert script.safe_eval_predicate(code, {"rsi": float("nan")}) is False
    gt = script.generate_python_predicate("q", "close > sma50")
    assert script.safe_eval_predicate(gt, {"close": 5.0}) is False  # sma50 missing


def test_lowercase_and_and_numeric_suffixes(script) -> None:
    code = script.generate_python_predicate("q", "Volume > 500k and close >= 10")
    assert script.safe_eval_predicate(code, {"volume": 600_000, "close": 10})
    assert not script.safe_eval_predicate(code, {"volume": 400_000, "close": 10})


# --- safe_eval_predicate: a real AST whitelist ------------------------------------------------

_PAYLOADS = [
    "def match(row):\n    return __import__('os').system('true')",
    "def match(row):\n    return row.get('volume', 0) > 5",
    "def match(row):\n    return row.__class__ is not None",
    "def match(row):\n    return (lambda: True)()",
    "def match(row):\n    return [x for x in (1, 2)]",
    "def match(row):\n    return row['volume'] > 5",
    "def match(row):\n    return volume > 5 and ().__class__",
    "def match(row):\n    return 9 ** 9 ** 9 > volume",
    "def match(row):\n    return volume > 5 if volume else False",
    "import os\ndef match(row):\n    return True",
    "def match(row):\n    import os\n    return True",
    "def match(row):\n    x = 1\n    return volume > x",
    "def match(row, other=__import__('os')):\n    return True",
    "@print\ndef match(row):\n    return True",
    "def match(row):\n    return open('/etc/passwd')",
    "def match(row):\n    return 'a' in 'abc'",
    "def other(row):\n    return True",
    "x = 1",
]


@pytest.mark.parametrize("code", _PAYLOADS)
def test_safe_eval_rejects_payloads(code: str) -> None:
    with pytest.raises(ValueError):
        safe_eval_predicate(code, {"volume": 10})


def test_safe_eval_does_not_execute_rejected_code(tmp_path) -> None:
    marker = tmp_path / "pwned"
    code = f"def match(row):\n    return open({str(marker)!r}, 'w').write('x')"
    with pytest.raises(ValueError):
        safe_eval_predicate(code, {})
    assert not marker.exists()


def test_safe_eval_unknown_name_is_rejected_not_defaulted() -> None:
    with pytest.raises(ValueError, match="unknown metric"):
        safe_eval_predicate("def match(row):\n    return mystery > 1", {"mystery": 5})


def test_safe_eval_allows_the_whitelisted_forms() -> None:
    code = "def match(row):\n    return not (close * 2 - 1 < sma50 / 2 % 7) or -volume < +3"
    assert isinstance(safe_eval_predicate(code, {"close": 5.0, "sma50": 4.0, "volume": 1.0}), bool)


# --- as-of date only from date phrasing --------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    ["volume > 2020", "close > 2019 and rsi < 30", "market_cap > 2018Cr", "price above 2022"],
)
def test_bare_number_is_not_an_asof_date(query: str) -> None:
    assert resolve_asof(query, None, None, False, TODAY) == (TODAY, False)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("rsi < 30 in 2020", dt.date(2020, 10, 11)),
        ("rsi < 30 as of 2019", dt.date(2019, 10, 11)),
        ("rsi < 30 as of 2021-03-05", dt.date(2021, 3, 5)),
        ("2018-06-01 rsi < 30", dt.date(2018, 6, 1)),
    ],
)
def test_dated_phrasing_is_an_asof_date(query: str, expected: dt.date) -> None:
    assert resolve_asof(query, None, None, False, TODAY) == (expected, True)


# --- run_nl_screener ordering and notes --------------------------------------------------------


class _FakeStore:
    """Daily bars per symbol: 80 sessions to 2021-12-31 plus a forward leg to 2022-06-30."""

    def read(self, inst, _timeframe, _interval):
        idx = sum(ord(ch) for ch in inst.symbol) % 7  # deterministic per-symbol drift
        start = dt.datetime(2021, 10, 1, tzinfo=dt.timezone.utc)
        bars = []
        for i in range(190):
            day = start + dt.timedelta(days=i)
            px = 100.0 + i * (idx - 3) * 0.2 + 50.0
            bars.append(
                SimpleNamespace(
                    ts=int(day.timestamp() * 1e9), open=px, high=px, low=px, close=px, volume=1e6
                )
            )
        return bars


def _run_hist(script, monkeypatch, limit):
    monkeypatch.setattr(script, "_STORE", _FakeStore())
    return script.run_nl_screener(
        "volume > 1000", backend="fake", asof="2021-12-30", backtest=True, limit=limit
    )


def test_historical_rows_are_not_sorted_by_forward_return(script, monkeypatch) -> None:
    res = _run_hist(script, monkeypatch, limit=5)
    rows = res["rows"]
    assert len(rows) == 5
    caps = [r["market_cap"] for r in rows]
    assert caps == sorted(caps, reverse=True)  # as-of key, known at the time
    winners = [f["symbol"] for f in _run_hist(script, monkeypatch, limit=5)["forward_returns"]][:5]
    assert [r["symbol"] for r in rows] != winners  # not the top of the winners
    full = _run_hist(script, monkeypatch, limit=1000)["rows"]
    assert [r["symbol"] for r in rows] == [r["symbol"] for r in full][:5]


def test_forward_returns_reported_separately_for_all_matches(script, monkeypatch) -> None:
    res = _run_hist(script, monkeypatch, limit=3)
    fwd = res["forward_returns"]
    assert len(fwd) == res["matched_count"] > 3
    rets = [f["return_pct"] for f in fwd]
    assert rets == sorted(rets, reverse=True)
    assert any("hindsight" in n for n in res["notes"])


def test_result_labels_market_cap_as_approximation(script, monkeypatch) -> None:
    res = _run_hist(script, monkeypatch, limit=3)
    assert any("market_cap is approximate" in n for n in res["notes"])


def test_main_reports_predicate_errors_without_traceback(script, capsys) -> None:
    assert script.main(["--query", "x", "--backend", "fake"]) in (0, 2)  # fake filter is valid

    # an unparseable filter surfaces as exit code 2 with the message on stderr
    class Bad:
        def complete(self, *a, **k):
            return "gibberish here"

    original = script.resolve_llm_provider
    script.resolve_llm_provider = lambda backend: Bad()
    try:
        assert script.main(["--query", "x"]) == 2
    finally:
        script.resolve_llm_provider = original
    assert "could not parse filter clause" in capsys.readouterr().err
