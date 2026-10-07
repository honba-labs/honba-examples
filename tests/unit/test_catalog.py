from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace

from honba_examples import catalog


def test_load_named_uses_loader(monkeypatch, tmp_path):
    calls = {}

    def fake_find_catalog(strategies_dir=None, *, search_from=None, environ=None):
        calls["find"] = (strategies_dir, search_from)
        return tmp_path / "catalog.json"

    def fake_load_catalog_strategy(name, catalog_path):
        calls["load"] = (name, catalog_path)
        return SimpleNamespace(name=name, path=catalog_path)

    import honba.strategies.loader as loader

    monkeypatch.setattr(loader, "find_catalog", fake_find_catalog)
    monkeypatch.setattr(loader, "load_catalog_strategy", fake_load_catalog_strategy)

    res = catalog.load_named("alpha30_equal_weight", strategies_dir=tmp_path, search_from=tmp_path / "here")
    assert res.name == "alpha30_equal_weight"
    assert calls["find"] == (tmp_path, tmp_path / "here")
    assert calls["load"][0] == "alpha30_equal_weight"
    assert calls["load"][1] == tmp_path / "catalog.json"


def test_load_named_defaults(monkeypatch, tmp_path):
    calls = {}

    def fake_find_catalog(strategies_dir=None, *, search_from=None, environ=None):
        calls["find"] = (strategies_dir, search_from)
        return tmp_path / "catalog.json"

    def fake_load_catalog_strategy(name, catalog_path):
        calls["load"] = (name, catalog_path)
        return SimpleNamespace(ok=True)

    import honba.strategies.loader as loader

    monkeypatch.setattr(loader, "find_catalog", fake_find_catalog)
    monkeypatch.setattr(loader, "load_catalog_strategy", fake_load_catalog_strategy)

    res = catalog.load_named("sma")
    assert res.ok is True
    assert calls["find"] == (None, None)
    assert calls["load"][0] == "sma"
