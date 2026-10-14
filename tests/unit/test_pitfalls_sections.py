"""Every numbered example script documents its backtest/domain pitfalls.

Each ``<dir>/NN_*.py`` module docstring must carry a ``Pitfalls::`` block of 3 to 6 bullets
that are specific to what that script does (lookahead, survivorship, overfitting, costs, data
quality, synthetic-data caveats, ...). ``PENDING_PITFALLS`` lists the scripts that have not
been done yet so the suite stays green during the chunked rollout; remove a path from it
in the same commit that adds the section. The set must end up empty.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIN_BULLETS = 3
MAX_BULLETS = 6

# Scripts whose docstring does not have a Pitfalls section yet (shrinks to empty).
PENDING_PITFALLS: frozenset[str] = frozenset(
    {
        "ai_research/01_llm_research_analyst.py",
        "ai_research/02_autoresearch_loop.py",
        "ai_research/03_nl_screener.py",
        "ai_research/04_nl_to_strategy.py",
        "ai_research/05_rl_training.py",
        "ai_research/06_mcp_integration.py",
        "ai_research/07_natural_language_critique.py",
        "ai_research/09_typed_llm_decision.py",
        "basic/01_connect_dhan.py",
        "basic/02_fetch_instruments.py",
        "basic/03_subscribe_quotes.py",
        "basic/04_place_order_paper.py",
        "basic/05_check_positions.py",
        "candles/01_historical_candles.py",
        "candles/02_realtime_candles.py",
        "candles/03_custom_aggregation.py",
        "candles/04_multi_timeframe.py",
        "mutual_funds/01_fetch_nav.py",
        "mutual_funds/02_sip_backtest.py",
        "mutual_funds/03_category_analysis.py",
        "mutual_funds/04_portfolio_optimization.py",
        "options/01_option_chain.py",
        "options/02_greeks_calculation.py",
        "options/03_backtest_straddle.py",
        "options/04_backtest_iron_condor.py",
        "options/05_expiry_day_strategy.py",
        "production/01_paper_trading.py",
        "production/02_live_trading_dhan.py",
        "production/03_multi_account.py",
        "production/04_monitoring.py",
        "production/05_deployment.py",
        "screening/01_multibagger_screener.py",
        "screening/02_value_52w_low_screener.py",
        "screening/03_dividend_yield_screener.py",
        "screening/04_intelligent_investor_screener.py",
        "screening/05_net_net_screener.py",
        "screening/06_run_chain.py",
        "storage/01_parquet_catalog.py",
        "storage/02_import_nse_bhavcopy.py",
        "storage/03_import_amfi_nav.py",
        "storage/04_export_tearsheet.py",
        "strategies/01_first_strategy.py",
        "strategies/02_with_indicators.py",
        "strategies/03_position_sizing.py",
        "strategies/04_risk_management.py",
        "strategies/05_multi_instrument.py",
        "strategies/06_equal_weight_rebalance.py",
        "strategies/07_declarative_long_short.py",
        "strategies/08_pipeline_intro.py",
        "strategies/09_multibagger_momentum.py",
        "strategies/10_dogs_of_nifty.py",
        "strategies/11_contrarian_value.py",
        "universes/01_nifty50_constituents.py",
        "universes/02_banknifty_constituents.py",
        "universes/03_alpha30_constituents.py",
        "universes/04_alpha30_generic_rebalancer.py",
        "universes/05_alpha30_custom_universe.py",
        "universes/06_alpha30_equal_weight_demo.py",
        "universes/07_alpha30_backtest.py",
        "universes/08_alpha30_union_ewr_backtest.py",
    }
)


def _scripts() -> list[str]:
    return sorted(p.relative_to(ROOT).as_posix() for p in ROOT.glob("*/[0-9][0-9]_*.py"))


def pitfall_bullets(doc: str | None) -> list[str] | None:
    """Bullets of the ``Pitfalls::`` block in ``doc`` (``None`` when there is no such block)."""
    lines = (doc or "").splitlines()
    for start, line in enumerate(lines):
        if line.strip() in ("Pitfalls::", "Pitfalls:"):
            header_indent = len(line) - len(line.lstrip())
            break
    else:
        return None
    bullets: list[str] = []
    for line in lines[start + 1 :]:
        if not line.strip():
            continue
        if len(line) - len(line.lstrip()) <= header_indent:
            break  # dedent: the block is over
        if line.lstrip().startswith("- "):
            bullets.append(line.strip())
    return bullets


def test_pitfall_parser_counts_bullets() -> None:
    doc = "Summary.\n\nPitfalls::\n\n    - one\n      wrapped\n    - two\n\nRun::\n\n    - not counted\n"
    assert pitfall_bullets(doc) == ["- one", "- two"]
    assert pitfall_bullets("Summary only.") is None


def test_scripts_are_discovered() -> None:
    assert len(_scripts()) >= 70


def test_pending_entries_exist() -> None:
    missing = sorted(set(PENDING_PITFALLS) - set(_scripts()))
    assert not missing, f"PENDING_PITFALLS names scripts that do not exist: {missing}"


@pytest.mark.parametrize("rel", _scripts())
def test_script_documents_pitfalls(rel: str) -> None:
    doc = ast.get_docstring(ast.parse((ROOT / rel).read_text()))
    bullets = pitfall_bullets(doc)
    if rel in PENDING_PITFALLS:
        pytest.skip("Pitfalls section pending")
    assert bullets is not None, f"{rel}: module docstring has no 'Pitfalls::' section"
    assert MIN_BULLETS <= len(bullets) <= MAX_BULLETS, (
        f"{rel}: expected {MIN_BULLETS}-{MAX_BULLETS} Pitfalls bullets, found {len(bullets)}"
    )
