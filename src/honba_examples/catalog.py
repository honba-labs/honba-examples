from __future__ import annotations

from pathlib import Path
from typing import Any


def load_named(
    name: str, strategies_dir: Path | None = None, search_from: Path | None = None
) -> Any:
    """Load a named catalog strategy (honba.strategies.loader.load_catalog_strategy/find_catalog)."""
    from honba.strategies.loader import find_catalog, load_catalog_strategy

    catalog_path = find_catalog(strategies_dir, search_from=search_from)
    return load_catalog_strategy(name, catalog_path)


def strategies_catalog_dir() -> Path:
    """The honba-strategies checkout: ``$HONBA_STRATEGIES_DIR``, else the sibling checkout.

    Raises ``honba.strategies.loader.CatalogError`` saying how to point at one when absent.
    """
    from honba.strategies.loader import find_catalog

    return find_catalog(search_from=Path(__file__))


def load_catalog_tool(module: str) -> Any:
    """Import ``tools.<module>`` from the honba-strategies catalog (e.g. ``validate_chain``).

    The catalog's ``tools`` package is imported by path with ``sys.path`` and ``sys.modules``
    restored afterwards, so it cannot shadow or be shadowed by another ``tools`` package.
    """
    import importlib
    import sys

    root = str(strategies_catalog_dir())
    saved = {k: sys.modules.pop(k) for k in list(sys.modules) if k.split(".")[0] == "tools"}
    sys.path.insert(0, root)
    try:
        loaded = importlib.import_module(f"tools.{module}")
        for k in [k for k in sys.modules if k.split(".")[0] == "tools"]:
            del sys.modules[k]
    finally:
        sys.path.remove(root)
        sys.modules.update(saved)
    return loaded
