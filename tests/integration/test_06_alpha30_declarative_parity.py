"""06 declarative Alpha 30 behaves like the imperative catalog strategy it replaces.

Both strategies replay the same synthetic bars (no network, no Parquet store); the
imperative one is read from the sibling ``honba-strategies`` checkout (or
``$HONBA_STRATEGIES_DIR``) and the test is skipped when it is not available.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from honba.strategies.config import StrategyConfig
from honba.strategies.context import LedgerContext
from honba.strategies.loader import CatalogError, find_catalog

EXAMPLE = (
    Path(__file__).resolve().parents[2] / "strategies" / "06_alpha30_equal_weight_declarative.py"
)
CAPITAL = 1_000_000.0
SESSIONS = 50  # initial rebalance + 3 more at the default 15-day cadence


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def example() -> ModuleType:
    return _load(EXAMPLE, "ex06_declarative")


@pytest.fixture(scope="module")
def imperative() -> type:
    try:
        catalog = find_catalog(search_from=EXAMPLE)
    except CatalogError as exc:
        pytest.skip(str(exc))
    path = catalog / "universe" / "alpha" / "equal_weight" / "strategy.py"
    if not path.is_file():
        pytest.skip(f"{path} not found")
    return _load(path, "alpha30_imperative").Alpha30EqualWeight


def _replay(example: ModuleType, strategy) -> tuple[dict, list]:
    strategy.bind(LedgerContext(cash=CAPITAL))
    probe = example.Alpha30EqualWeightDeclarative()
    symbols = sorted(i.symbol for i in probe.universe())
    result = example._replay_at_last_close(strategy, example._synthetic_bars(symbols, SESSIONS))
    fills = sorted((f.ts, f.instrument_id.symbol, f.side.value, f.quantity) for f in result.fills)
    return strategy.ctx.positions(), fills


def test_declarative_matches_imperative_positions_and_fills(example, imperative) -> None:
    declared = example.Alpha30EqualWeightDeclarative()
    coded = imperative(StrategyConfig(name="alpha30_equal_weight", symbol="NIFTY"))
    declared_positions, declared_fills = _replay(example, declared)
    coded_positions, coded_fills = _replay(example, coded)
    assert len(declared_positions) == 30
    assert declared_positions == coded_positions
    assert declared_fills == coded_fills
    assert declared.ctx.cash() == coded.ctx.cash()
