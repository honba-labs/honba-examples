from __future__ import annotations

from pathlib import Path
from typing import Any


def load_named(name: str, strategies_dir: Path | None = None, search_from: Path | None = None) -> Any:
    """Load a named catalog strategy (honba.strategies.loader.load_catalog_strategy/find_catalog)."""
    from honba.strategies.loader import find_catalog, load_catalog_strategy

    catalog_path = find_catalog(strategies_dir, search_from=search_from)
    return load_catalog_strategy(name, catalog_path)
