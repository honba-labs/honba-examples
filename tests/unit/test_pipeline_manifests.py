"""Unit: ``describe()`` manifests of the pipeline steps follow the honba-strategies step schema."""

from __future__ import annotations

import inspect
import json

import pytest

from honba_examples.pipeline import (
    BreakoutSignal,
    DividendYieldScreener,
    EqualWeightSizer,
    FixedSipSizer,
    FundamentalQualityFilter,
    InverseVolatilitySizer,
    LiquidityFilter,
    MarketRegimeFilter,
    MultibaggerScreener,
    SipBacktestHarness,
    ValueScreener,
)

STEPS = [
    MultibaggerScreener,
    ValueScreener,
    DividendYieldScreener,
    FundamentalQualityFilter,
    MarketRegimeFilter,
    LiquidityFilter,
    FixedSipSizer,
    EqualWeightSizer,
    InverseVolatilitySizer,
    SipBacktestHarness,
]


def _schema_tools():
    from honba.strategies.loader import CatalogError

    from honba_examples.catalog import load_catalog_tool

    try:
        return load_catalog_tool("validate_chain")
    except CatalogError as exc:
        pytest.skip(f"honba-strategies catalog not available: {exc}")


@pytest.mark.parametrize("cls", STEPS, ids=lambda c: c.__name__)
def test_manifest_passes_schema(cls) -> None:
    tools = _schema_tools()
    assert tools.schema_errors(cls.describe()) == []


def test_manifest_ids_unique_and_kind_matches_id() -> None:
    manifests = [cls.describe() for cls in STEPS]
    ids = [m["id"] for m in manifests]
    assert len(ids) == len(set(ids))
    assert all(m["id"].split(".")[0] == m["kind"] for m in manifests)


def test_manifests_are_json_serialisable() -> None:
    json.dumps([cls.describe() for cls in STEPS])


@pytest.mark.parametrize("cls", STEPS, ids=lambda c: c.__name__)
def test_param_defaults_match_constructor(cls) -> None:
    """The manifest must describe the real constructor: same names, same defaults."""
    sig = inspect.signature(cls.__init__).parameters
    for name, spec in cls.describe()["params"].items():
        assert name in sig, f"{cls.__name__} has no constructor argument {name!r}"
        default = sig[name].default
        if spec.get("optional"):
            assert default is None and "default" not in spec
        else:
            assert spec["default"] == default


def test_steps_without_a_manifest_say_so() -> None:
    with pytest.raises(NotImplementedError, match="BreakoutSignal"):
        BreakoutSignal.describe()


def test_shapes_chain_from_screen_to_execute() -> None:
    shapes = [cls.describe() for cls in (ValueScreener, FundamentalQualityFilter, EqualWeightSizer)]
    assert [m["consumes"]["shape"] for m in shapes] == ["universe", "candidates", "candidates"]
    assert [m["emits"]["shape"] for m in shapes] == ["candidates", "candidates", "weights"]
