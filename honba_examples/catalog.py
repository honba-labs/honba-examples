"""Load a strategy from the ``honba-strategies`` catalog by its registry name.

The catalog is a sibling checkout, not an installed package, and the core has no
loader for it yet (``honba.session`` refers to a ``honba.strategies.loader`` that
does not exist). Strategies are found through the catalog's ``registry.json``
(top level, then per-strategy ``registry.json`` files) rather than a hardcoded path.

Catalog location, first match wins: an explicit path (``--strategies-dir``),
``$HONBA_STRATEGIES_DIR``, then ``honba-strategies`` next to this repo.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from honba.strategies.base import Strategy
from honba.strategies.config import StrategyConfig

__all__ = [
    "ENV_VAR",
    "CatalogError",
    "CatalogStrategy",
    "find_catalog",
    "load_catalog_strategy",
]

ENV_VAR = "HONBA_STRATEGIES_DIR"
REPO_ROOT = Path(__file__).resolve().parents[1]


class CatalogError(RuntimeError):
    """The catalog or a strategy in it could not be found or loaded."""


@dataclass(frozen=True)
class CatalogStrategy:
    name: str
    path: Path  # strategy directory
    cls: type[Strategy]
    config: StrategyConfig
    module: ModuleType
    source_sha256: str  # sha256 of strategy.py + config.toml, for run provenance


def _is_catalog(path: Path) -> bool:
    return (path / "registry.json").is_file()


def find_catalog(
    explicit: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    repo_root: Path = REPO_ROOT,
) -> Path:
    """Return the catalog root, or raise ``CatalogError`` saying how to point at one."""
    environ = os.environ if environ is None else environ
    if explicit is not None:
        if not _is_catalog(Path(explicit)):
            raise CatalogError(f"{explicit} is not a honba-strategies catalog (no registry.json)")
        return Path(explicit).resolve()
    env = environ.get(ENV_VAR)
    if env:
        if not _is_catalog(Path(env)):
            raise CatalogError(f"${ENV_VAR}={env} is not a catalog (no registry.json)")
        return Path(env).resolve()
    sibling = repo_root.parent / "honba-strategies"
    if _is_catalog(sibling):
        return sibling.resolve()
    raise CatalogError(
        "honba-strategies catalog not found. Clone it next to this repo, or pass "
        f"--strategies-dir PATH, or set ${ENV_VAR}."
    )


def _registry_entries(catalog: Path) -> dict[str, str]:
    """``{name: relative path}`` from the top-level and per-strategy registries."""
    entries: dict[str, str] = {}
    top = json.loads((catalog / "registry.json").read_text(encoding="utf-8"))
    for e in top.get("strategies", []):
        entries.setdefault(e["name"], e["path"])
    for reg in sorted(catalog.glob("**/registry.json")):
        if reg.parent == catalog:
            continue
        e = json.loads(reg.read_text(encoding="utf-8"))
        if isinstance(e, dict) and "name" in e:
            entries.setdefault(e["name"], e.get("path") or str(reg.parent.relative_to(catalog)))
    return entries


def _import(strategy_dir: Path, catalog: Path, name: str) -> ModuleType:
    # Same path setup as the catalog's own conftest, so sibling imports resolve.
    for p in (strategy_dir, strategy_dir.parent, strategy_dir.parent.parent, catalog):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    spec = importlib.util.spec_from_file_location(f"catalog_{name}", strategy_dir / "strategy.py")
    if spec is None or spec.loader is None:
        raise CatalogError(f"cannot import {strategy_dir / 'strategy.py'}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_catalog_strategy(name: str, catalog: Path) -> CatalogStrategy:
    """Import the strategy registered as ``name`` and read its ``config.toml``."""
    catalog = Path(catalog).resolve()
    entries = _registry_entries(catalog)
    if name not in entries:
        raise CatalogError(
            f"no strategy named {name!r} in {catalog}; available: {', '.join(sorted(entries))}"
        )
    strategy_dir = (catalog / entries[name]).resolve()
    module = _import(strategy_dir, catalog, name)
    classes = [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, Strategy) and obj is not Strategy and obj.__module__ == module.__name__
    ]
    named = [c for c in classes if getattr(c, "name", None) == name]
    if len(named) == 1:
        cls = named[0]
    elif len(classes) == 1:
        cls = classes[0]
    else:
        raise CatalogError(f"{strategy_dir / 'strategy.py'}: expected one Strategy named {name!r}")
    digest = hashlib.sha256()
    for f in ("strategy.py", "config.toml"):
        digest.update((strategy_dir / f).read_bytes())
    return CatalogStrategy(
        name=name,
        path=strategy_dir,
        cls=cls,
        config=StrategyConfig.from_toml(strategy_dir / "config.toml"),
        module=module,
        source_sha256=digest.hexdigest(),
    )
