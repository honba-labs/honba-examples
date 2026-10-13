"""tests/unit/test_pipeline.py: Unit tests for the composable pipeline DSL (Phase 3)."""

from __future__ import annotations

import datetime as dt

import pytest

from honba_examples.pipeline import (
    BreakoutSignal,
    BuyAndHoldSignal,
    DividendYieldScreener,
    EqualWeightSizer,
    FixedSipSizer,
    FundamentalQualityFilter,
    InverseVolatilitySizer,
    LiquidityFilter,
    MarketRegimeFilter,
    MultibaggerScreener,
    Pipeline,
    SipBacktestHarness,
    Step,
    ValueScreener,
    parse_symbols,
)
from honba_examples.pipeline.base import PipelineResult


class _RecordStep(Step):
    """Test step that records a value onto the context."""

    def __init__(self, name: str, key: str, value: object) -> None:
        self.name = name
        self.key = key
        self.value = value

    def run(self, ctx: dict) -> dict:
        ctx[self.key] = self.value
        return ctx


def test_operator_overload_chains_into_pipeline() -> None:
    a = _RecordStep("a", "a", 1)
    b = _RecordStep("b", "b", 2)

    pipeline = a >> b
    assert isinstance(pipeline, Pipeline)
    assert [s.name for s in pipeline.steps] == ["a", "b"]

    piped = a | b
    assert isinstance(piped, Pipeline)
    assert len(piped) == 2

    chained = a >> b >> _RecordStep("c", "c", 3)
    assert [s.name for s in chained.steps] == ["a", "b", "c"]

    nested = Pipeline([a]) >> Pipeline([b])
    assert [s.name for s in nested.steps] == ["a", "b"]


def test_pipeline_run_flows_context() -> None:
    pipeline = _RecordStep("a", "a", 1) >> _RecordStep("b", "b", 2)
    result = pipeline.run()
    assert isinstance(result, PipelineResult)
    assert result["a"] == 1 and result["b"] == 2
    assert pipeline.run({"a": 99})["a"] == 1
    assert pipeline.append(_RecordStep("c", "c", 3)).run()["c"] == 3


def test_multibagger_screener_returns_candidates() -> None:
    result = MultibaggerScreener(asof="2024-01-01", top=3).run({})
    assert result["symbols"]
    assert len(result["symbols"]) <= 3
    assert result["candidates"][0]["symbol"] in result["symbols"]
    assert result["candidates"][0]["close"] > 0


def test_value_and_dividend_screeners_produce_symbols() -> None:
    value = ValueScreener(asof="2025-03-01", max_pe=20.0, max_pb=2.0, top=3).run({})
    assert all(c["pe_ratio"] is None or c["pe_ratio"] <= 20.0 for c in value["candidates"])

    dividend = DividendYieldScreener(asof="2025-03-01", min_yield_pct=3.0, top=3).run({})
    assert all(c["div_yield_pct"] >= 3.0 for c in dividend["candidates"])


def test_fundamental_quality_filter_filters_by_roce() -> None:
    ctx = {
        "candidates": [
            {
                "symbol": "PASS",
                "roce_pct": 18.0,
                "debt_to_equity": 0.4,
                "operating_margin_pct": 12.0,
            },
            {"symbol": "LOW_ROCE", "roce_pct": 8.0, "debt_to_equity": 0.4},
            {"symbol": "LEVERED", "roce_pct": 25.0, "debt_to_equity": 2.0},
            {"symbol": "THIN_MARGIN", "roce_pct": 25.0, "operating_margin_pct": -1.0},
            {"symbol": "UNKNOWN", "roce_pct": None, "debt_to_equity": None},
        ],
        "symbols": ["PASS", "LOW_ROCE", "LEVERED", "THIN_MARGIN", "UNKNOWN"],
    }
    result = FundamentalQualityFilter(
        min_roce=15.0, max_debt_to_equity=1.0, min_operating_margin=0.0
    ).run(ctx)
    assert result["symbols"] == ["PASS", "UNKNOWN"]


def test_market_regime_filter_passes_and_blocks() -> None:
    ctx = {
        "symbols": ["RELIANCE", "TCS"],
        "candidates": [{"symbol": "RELIANCE"}, {"symbol": "TCS"}],
        "asof": dt.date(2024, 6, 3),
    }
    bullish = MarketRegimeFilter(benchmark="RELIANCE", condition="above_sma200").run(dict(ctx))
    bearish = MarketRegimeFilter(benchmark="RELIANCE", condition="below_sma200").run(dict(ctx))
    assert bullish["regime"]["above_sma"] is True
    assert bullish["symbols"] == ["RELIANCE", "TCS"]
    assert bearish["symbols"] == []
    assert bearish["candidates"] == []

    missing = MarketRegimeFilter(benchmark="NIFTY").run(dict(ctx))
    assert missing["symbols"] == ["RELIANCE", "TCS"]
    assert "not found" in missing["regime"]["reason"]


def test_liquidity_filter_thresholds() -> None:
    ctx = {
        "symbols": ["RELIANCE", "TCS"],
        "candidates": [{"symbol": "RELIANCE"}, {"symbol": "TCS"}],
        "asof": dt.date(2024, 6, 3),
    }
    assert LiquidityFilter(min_avg_turnover=0.0).run(dict(ctx))["symbols"] == ["RELIANCE", "TCS"]
    assert LiquidityFilter(min_avg_turnover=1e18).run(dict(ctx))["symbols"] == []


def test_sizers_produce_weights_summing_to_one() -> None:
    ctx = {"symbols": ["AAA", "BBB", "CCC"]}
    for sizer in (FixedSipSizer(monthly_amount=10000.0), EqualWeightSizer()):
        weights = sizer.run(dict(ctx))["weights"]
        assert set(weights) == {"AAA", "BBB", "CCC"}
        assert sum(weights.values()) == pytest.approx(1.0)

    fixed = FixedSipSizer(monthly_amount=5000.0).run(dict(ctx))
    assert fixed["monthly_amount"] == 5000.0

    vol_ctx = {"symbols": ["RELIANCE", "TCS"], "asof": dt.date(2024, 6, 3)}
    vol_weights = InverseVolatilitySizer().run(vol_ctx)["weights"]
    assert sum(vol_weights.values()) == pytest.approx(1.0)


def test_signal_stubs_are_pass_through() -> None:
    ctx = {"symbols": ["RELIANCE"], "candidates": [{"symbol": "RELIANCE"}]}
    assert BuyAndHoldSignal().run(dict(ctx))["signal"] == "buy_and_hold"
    breakout = BreakoutSignal(lookback=10).run(dict(ctx))
    assert breakout["signal"] == "breakout"
    assert breakout["symbols"] == ["RELIANCE"]


def test_end_to_end_pipeline_returns_summary() -> None:
    pipeline = (
        MultibaggerScreener(asof="2024-01-01", top=3)
        >> FundamentalQualityFilter(min_roce=0.0, max_debt_to_equity=5.0)
        >> FixedSipSizer(monthly_amount=10000.0)
        >> SipBacktestHarness(asof="2024-01-01", end_date="2025-01-01")
    )
    assert isinstance(pipeline, Pipeline)
    results = pipeline.run()
    assert results.summary is not None
    assert results.summary["candidate_count"] > 0
    assert results.summary["total_invested"] > 0
    assert results.stocks
    assert results.weights
    results.print_summary()


def test_parse_symbols_matches_cli_stream_contract() -> None:
    assert parse_symbols("") == []
    assert parse_symbols("tcs, infy ,reliance") == ["TCS", "INFY", "RELIANCE"]
    assert parse_symbols('{"candidates": [{"symbol": "tcs"}, {"symbol": "infy"}]}') == [
        "TCS",
        "INFY",
    ]
    assert parse_symbols('{"symbols": ["tcs", "infy"]}') == ["TCS", "INFY"]
    assert parse_symbols("{not json") == ["{NOT", "JSON"]
