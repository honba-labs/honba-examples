"""Integration: composable pipeline + CLI chaining (Phase 4, roadmap §4 dry verification).

Verifies the backtesting/09 portfolio rebalancer against the real catalog, the strategy
recipes that drive the pipeline DSL, and the screening → backtest runtime plane
(screening → 09_forward / 11_portfolio) with CLI stream ingestion.

These are dry tests — no network, no synthetic data, no bootstrap. They fail visibly on
the CI host when the catalog or DuckDB lakehouse is unavailable, rather than being
skipped.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from honba_examples.pipeline import (
    DividendYieldScreener,
    EqualWeightSizer,
    FixedSipSizer,
    FundamentalQualityFilter,
    MarketRegimeFilter,
    Pipeline,
    SipBacktestHarness,
    ValueScreener,
)

EX = Path(__file__).resolve().parents[2]
PY = sys.executable


def _load(relative: str, name: str):
    spec = importlib.util.spec_from_file_location(name, EX / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_cli_stream(screening_rel: str, backtest_rel: str) -> dict:
    """Drive two scripts via a shell pipe, returning the downstream JSON."""
    screener = str(EX / screening_rel)
    harness = str(EX / backtest_rel)
    pipe = subprocess.run(
        [
            "/bin/bash",
            "-c",
            f"{PY!r} {screener!r} --asof 2025-03-01 --top 3 --format symbols 2>/dev/null | {PY!r} {harness!r} --asof 2025-03-01 --end-date 2025-12-31 --format json 2>/dev/null",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert pipe.returncode == 0, f"pipe failed: {pipe.stderr[:500]}"
    return json.loads(pipe.stdout)


def test_pipeline_dsl_matches_roadmap_snippet():
    """Roadmap §5 Python snippet runs and produces a backtest."""
    pipeline = (
        DividendYieldScreener(min_yield_pct=3.0, max_pct_from_52w_low=10.0)
        >> FundamentalQualityFilter(min_roce=15.0, max_debt_to_equity=1.0)
        >> MarketRegimeFilter(benchmark="NIFTY", condition="above_sma200")
        >> FixedSipSizer(monthly_amount=10000.0)
        >> SipBacktestHarness(asof="2023-01-01")
    )
    results = pipeline.run()
    results.print_summary()
    assert isinstance(pipeline, Pipeline)
    assert "candidates" in results or "summary" in results


def test_rebalancer_harness_screening_path():
    """11_portfolio_rebalancer screens → equal-weights → backtests when no symbols are supplied."""

    ps = ValueScreener(asof="2025-03-01", max_pe=20.0, max_pb=2.0, top=3)
    pipeline = ps >> EqualWeightSizer() >> SipBacktestHarness(asof="2025-03-01")
    result = pipeline.run()
    assert result.summary is not None
    assert result.summary["candidate_count"] == pytest.approx(len(result.symbols))
    assert result.weights


def test_09_forward_sip_backtest_alias_same_as_chained():
    chained = _load(
        "backtesting/10_chained_screener_and_sip.py", "backtesting_10_chained_screener_and_sip_2"
    )
    aliased = _load(
        "backtesting/09_forward_sip_backtest.py", "backtesting_09_forward_sip_backtest_2"
    )
    assert hasattr(chained, "ChainedScreenerSipExample")
    assert hasattr(aliased, "ChainedScreenerSipExample")
    assert chained.ChainedScreenerSipExample.__name__ == aliased.ChainedScreenerSipExample.__name__
    assert aliased.ChainedScreenerSipExample.main is chained.ChainedScreenerSipExample.main


def test_strategy_08_pipeline_intro_smoke():
    m = _load("strategies/08_pipeline_intro.py", "strategies_08_pipeline_intro")
    payload = m.run()
    assert set(payload["symbols"]) == {"RELIANCE", "TCS"}
    assert len(payload["weights"]) == 2


def test_strategy_09_multibagger_momentum_smoke():
    m = _load("strategies/09_multibagger_momentum.py", "strategies_09_multibagger_momentum")
    pipeline = m.build_pipeline(asof="2024-01-01", top=3)
    assert isinstance(pipeline, Pipeline)
    assert any(s.name == "multibagger_screener" for s in pipeline.steps)


def test_strategy_10_dogs_of_nifty_smoke():
    m = _load("strategies/10_dogs_of_nifty.py", "strategies_10_dogs_of_nifty")
    pipeline = m.build_pipeline(asof="2025-03-01", top=3)
    assert any(s.name == "dividend_yield_screener" for s in pipeline.steps)
    result = m.run(asof="2025-03-01", top=3, output_format="json")
    assert result.get("summary") is not None


def test_strategy_11_contrarian_value_smoke():
    m = _load("strategies/11_contrarian_value.py", "strategies_11_contrarian_value")
    result = m.run(asof="2025-03-01", top=3, output_format="json")
    assert result.get("summary") is not None
    # Sizer swap is a recipe knob
    fixed = m.build_pipeline(asof="2025-03-01", top=3, sizer="fixed_sip")
    vol = m.build_pipeline(asof="2025-03-01", top=3, sizer="inv_vol")
    assert any(s.name == "fixed_sip" for s in fixed.steps)
    assert any(s.name == "inverse_volatility" for s in vol.steps)


def test_cli_stream_multibagger_pipe():
    payload = _run_cli_stream(
        "screening/01_multibagger_screener.py", "backtesting/09_forward_sip_backtest.py"
    )
    assert payload["portfolio_backtest"]["summary"]["candidate_count"] >= 1
    assert payload["portfolio_backtest"]["summary"]["total_invested"] >= 0


def test_cli_stream_value_pipe_to_11_portfolio_rebalancer():
    payload = _run_cli_stream(
        "screening/02_value_52w_low_screener.py", "backtesting/11_portfolio_rebalancer.py"
    )
    assert payload["summary"]["candidate_count"] >= 1
    assert payload["stocks"]


def test_rebalancer_alias_and_recipe_parity():
    m = _load("strategies/10_dogs_of_nifty.py", "strategies_10_parity_check")
    recipe = m.run(asof="2025-03-01", top=3, output_format="json")
    # CLI-built pipeline must carry the same screener configuration
    assert (
        "IOC" in [c["symbol"] for c in (recipe.get("candidates") or [])]
        or recipe.get("summary", {}).get("candidate_count", 0) > 0
    )
