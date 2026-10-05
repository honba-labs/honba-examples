"""Loading strategies from the honba-strategies catalog by registry name."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from honba_examples.catalog import CatalogError, find_catalog, load_catalog_strategy

STRATEGY_SRC = """
from honba.strategies.base import Strategy


class Probe(Strategy):
    name = "probe"

    def __init__(self, config):
        self.config = config
"""


def make_catalog(root: Path, *, top_level: bool) -> Path:
    d = root / "group" / "probe"
    d.mkdir(parents=True)
    (d / "strategy.py").write_text(STRATEGY_SRC)
    (d / "config.toml").write_text('name = "probe"\nsymbol = "MULTI"\n\n[params]\nx = 1\n')
    entry = {"name": "probe", "path": "group/probe", "category": "test"}
    if top_level:
        (root / "registry.json").write_text(json.dumps({"strategies": [entry]}))
    else:
        (root / "registry.json").write_text(json.dumps({"strategies": []}))
        (d / "registry.json").write_text(json.dumps(entry))
    return root


@pytest.mark.parametrize("top_level", [True, False])
def test_loads_strategy_class_config_and_source_hash(tmp_path: Path, top_level: bool) -> None:
    cat = make_catalog(tmp_path / "cat", top_level=top_level)
    loaded = load_catalog_strategy("probe", cat)
    assert loaded.cls.name == "probe"
    assert loaded.config.params["x"] == 1
    assert loaded.path == (cat / "group" / "probe").resolve()
    assert len(loaded.source_sha256) == 64


def test_unknown_name_lists_what_is_available(tmp_path: Path) -> None:
    cat = make_catalog(tmp_path / "cat", top_level=True)
    with pytest.raises(CatalogError, match="probe"):
        load_catalog_strategy("nope", cat)


def test_find_catalog_precedence(tmp_path: Path) -> None:
    explicit = make_catalog(tmp_path / "explicit", top_level=True)
    env = make_catalog(tmp_path / "env", top_level=True)
    sibling = make_catalog(tmp_path / "honba-strategies", top_level=True)
    repo = tmp_path / "honba-examples"
    repo.mkdir()
    environ = {"HONBA_STRATEGIES_DIR": str(env)}
    assert find_catalog(explicit, environ=environ, repo_root=repo) == explicit.resolve()
    assert find_catalog(None, environ=environ, repo_root=repo) == env.resolve()
    assert find_catalog(None, environ={}, repo_root=repo) == sibling.resolve()


def test_find_catalog_error_says_how_to_fix(tmp_path: Path) -> None:
    repo = tmp_path / "honba-examples"
    repo.mkdir()
    with pytest.raises(CatalogError, match="--strategies-dir|HONBA_STRATEGIES_DIR"):
        find_catalog(None, environ={}, repo_root=repo)
    with pytest.raises(CatalogError, match="registry.json"):
        find_catalog(tmp_path, environ={}, repo_root=repo)
