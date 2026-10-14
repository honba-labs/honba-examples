"""Unit: chain loading, validation against the honba-strategies schema, and pipeline building."""

from __future__ import annotations

from pathlib import Path

import pytest
from honba.strategies.loader import CatalogError

from honba_examples.pipeline import (
    EqualWeightSizer,
    FundamentalQualityFilter,
    Pipeline,
    ValueScreener,
)
from honba_examples.pipeline.chain import (
    REGISTRY,
    ChainError,
    ChainNotRunnableError,
    build_pipeline,
    describe_all,
    load_chain,
    validate,
)

CHAIN_TOML = """
[[steps]]
uses = "screen.value"
with = { asof = 2025-03-01, max_pe = 0, top = 5 }

[[steps]]
uses = "filter.fundamental_quality"
with = { min_roce = 12.0 }
report = ["market_cap", "dividend_yield_pct"]

[[steps]]
uses = "size.equal_weight"
"""


@pytest.fixture(autouse=True)
def _need_catalog() -> None:
    try:
        validate([{"uses": "size.equal_weight"}])
    except CatalogError as exc:
        pytest.skip(f"honba-strategies catalog not available: {exc}")


@pytest.fixture
def chain_file(tmp_path: Path) -> Path:
    path = tmp_path / "chain.toml"
    path.write_text(CHAIN_TOML)
    return path


def test_registry_ids_match_manifests() -> None:
    assert set(REGISTRY) == set(describe_all())
    assert all(REGISTRY[sid].describe()["id"] == sid for sid in REGISTRY)


def test_load_chain_normalises_toml(chain_file: Path) -> None:
    chain = load_chain(chain_file)
    assert [s["uses"] for s in chain] == [
        "screen.value",
        "filter.fundamental_quality",
        "size.equal_weight",
    ]
    assert chain[0]["with"]["asof"] == "2025-03-01"  # TOML date becomes an ISO string
    assert chain[1]["report"] == ["market_cap", "dividend_yield_pct"]
    assert chain[2] == {"uses": "size.equal_weight", "with": {}, "report": []}
    assert load_chain({"steps": [{"id": "size.equal_weight"}]})[0]["uses"] == "size.equal_weight"


def test_load_chain_rejects_bad_structure() -> None:
    with pytest.raises(ChainError, match="non-empty"):
        load_chain({"steps": []})
    with pytest.raises(ChainError, match="missing 'uses'"):
        load_chain([{"with": {}}])
    with pytest.raises(ChainError, match="unknown key 'bogus'"):
        load_chain([{"uses": "screen.value", "bogus": 1}])
    with pytest.raises(ChainError, match="'report' must be a list"):
        load_chain([{"uses": "screen.value", "report": "market_cap"}])


def test_valid_chain_passes(chain_file: Path) -> None:
    assert validate(load_chain(chain_file)) == []


def test_validation_errors() -> None:
    (unknown,) = validate([{"uses": "screen.nope"}])
    assert "unknown step id 'screen.nope'" in unknown and "screen.value" in unknown

    errors = validate(
        [
            {"uses": "screen.value", "with": {"max_pe": -1, "oops": 1, "top": 0}, "report": ["x"]},
            {"uses": "size.equal_weight"},
            {"uses": "filter.liquidity"},
        ]
    )
    text = "\n".join(errors)
    assert "max_pe: -1 below min" in text
    assert "top: 0 below min" in text
    assert "unknown param 'oops'" in text
    assert "cannot report 'x'" in text and "market_cap" in text
    assert "cannot follow 'size'" in text
    assert "consumes shape 'candidates' but size.equal_weight emits 'weights'" in text


def test_report_of_a_native_column_is_rejected() -> None:
    errors = validate([{"uses": "screen.value", "report": ["pe_ratio"]}])
    assert any("cannot report 'pe_ratio'" in e for e in errors)  # the screener already emits it


def test_first_step_must_start_from_a_universe() -> None:
    errors = validate([{"uses": "filter.liquidity"}])
    assert any("first step must consume" in e for e in errors)


def test_build_pipeline_maps_params_and_report(chain_file: Path) -> None:
    pipeline = build_pipeline(load_chain(chain_file))
    assert isinstance(pipeline, Pipeline)
    screen, quality, sizer = pipeline.steps
    assert isinstance(screen, ValueScreener) and isinstance(sizer, EqualWeightSizer)
    assert isinstance(quality, FundamentalQualityFilter)
    assert screen.asof == "2025-03-01" and screen.top == 5
    assert screen.max_pe is None  # 0 in a chain file means "test disabled"
    assert screen.max_pb == 2.0  # untouched default
    assert quality.min_roce == 12.0 and quality.report == ["market_cap", "dividend_yield_pct"]


def test_build_pipeline_refuses_invalid_chain() -> None:
    with pytest.raises(ChainError, match="unknown step id"):
        build_pipeline([{"uses": "screen.nope"}])


def test_catalog_dir_override_reaches_steps(chain_file: Path, tmp_path: Path) -> None:
    pipeline = build_pipeline(load_chain(chain_file), catalog_dir=tmp_path)
    assert pipeline.steps[0].catalog_dir == tmp_path  # type: ignore[attr-defined]


def test_missing_catalog_is_a_clear_error(
    chain_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HONBA_STRATEGIES_DIR", str(tmp_path))
    with pytest.raises(CatalogError, match="HONBA_STRATEGIES_DIR"):
        validate(load_chain(chain_file))


PORTFOLIO_CHAIN = [
    {"uses": "screen.value"},
    {"uses": "filter.liquidity"},
    {"uses": "size.equal_weight"},
    {"uses": "execute.equal_weight"},  # a honba-strategies catalog step
]


def test_catalog_portfolio_step_validates_after_a_sizer() -> None:
    assert validate(PORTFOLIO_CHAIN) == []


def test_catalog_portfolio_step_validates_straight_from_a_universe() -> None:
    assert validate([{"uses": "execute.alpha30_momentum"}]) == []
    assert (
        validate([{"uses": "execute.alpha30_momentum", "with": {"select": "top:3:momentum:60"}}])
        == []
    )


def test_catalog_step_params_are_checked() -> None:
    errors = validate([{"uses": "execute.alpha30_momentum", "with": {"nope": 1}}])
    assert any("unknown param 'nope'" in e for e in errors)


def test_signal_then_sizer_is_rejected_with_a_helpful_message() -> None:
    errors = validate([{"uses": "signal.sma_crossover"}, {"uses": "size.equal_weight"}])
    assert len(errors) == 1
    assert "emits 'orders'" in errors[0] and "screen" in errors[0] and "filter" in errors[0]


def test_unknown_id_message_mentions_catalog_ids() -> None:
    (unknown,) = validate([{"uses": "execute.nope"}])
    assert "unknown step id 'execute.nope'" in unknown
    assert "execute.equal_weight" in unknown and "execute.sip_backtest" in unknown


def test_catalog_step_is_validated_but_not_runnable() -> None:
    with pytest.raises(ChainNotRunnableError, match="validated but not runnable"):
        build_pipeline(PORTFOLIO_CHAIN)
    with pytest.raises(ChainError, match="execute.alpha30_momentum"):
        build_pipeline([{"uses": "execute.alpha30_momentum"}])
