"""06 code-composed PortfolioStrategy behaves like the catalog EqualWeightRebalance.

Both strategies replay the same synthetic bars (no network, no Parquet store); the
catalog one (``portfolio/rebalancing/equal_weight``) is read from the sibling ``honba-strategies`` checkout (or
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

EXAMPLE = Path(__file__).resolve().parents[2] / "strategies" / "06_equal_weight_rebalance.py"
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
    return _load(EXAMPLE, "ex06_composed")


@pytest.fixture(scope="module")
def catalog_strategy() -> tuple[type, StrategyConfig]:
    try:
        catalog = find_catalog(search_from=EXAMPLE)
    except CatalogError as exc:
        pytest.skip(str(exc))
    path = catalog / "portfolio" / "rebalancing" / "equal_weight" / "strategy.py"
    if not path.is_file():
        pytest.skip(f"{path} not found")
    cls = _load(path, "catalog_equal_weight").EqualWeightRebalance
    return cls, StrategyConfig.from_toml(path.parent / "config.toml")


def _replay(example: ModuleType, strategy) -> tuple[dict, list]:
    strategy.bind(LedgerContext(cash=CAPITAL))
    probe = example.build_strategy()
    symbols = sorted(i.symbol for i in probe.universe())
    result = example._replay_at_last_close(strategy, example._synthetic_bars(symbols, SESSIONS))
    fills = sorted((f.ts, f.instrument_id.symbol, f.side.value, f.quantity) for f in result.fills)
    return strategy.ctx.positions(), fills


def test_composed_matches_catalog_positions_and_fills(example, catalog_strategy) -> None:
    composed = example.build_strategy()  # catalog defaults: every 15 days, allocation 0.995
    cls, config = catalog_strategy
    coded = cls(config)
    assert coded.allocation == composed.allocation  # config.toml and the example agree
    composed_positions, composed_fills = _replay(example, composed)
    coded_positions, coded_fills = _replay(example, coded)
    assert len(composed_positions) == 30
    assert composed_positions == coded_positions
    assert composed_fills == coded_fills
    assert composed.ctx.cash() == coded.ctx.cash()
