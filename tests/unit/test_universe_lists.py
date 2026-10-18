"""One source for the Alpha 30 lists, and an honest Bank Nifty failure."""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import pytest
from honba.markets.india.universes import (
    Constituent,
    UniverseHistory,
    resolve_universe,
    save_universe_history,
)

from honba_examples.universe_lists import (
    ALPHA30_ENGINE,
    ALPHA30_SEED,
    UniverseUnavailable,
    alpha30_instruments,
    alpha30_symbols,
    banknifty_members,
)

UNIVERSES_DIR = Path(__file__).resolve().parents[2] / "examples" / "universes"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"t_{name}", UNIVERSES_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_engine_list_is_what_resolve_universe_returns():
    engine = [i.symbol for i in resolve_universe("nifty200_alpha_30")]
    assert list(ALPHA30_ENGINE) == engine
    assert alpha30_symbols() == ALPHA30_ENGINE


def test_seed_is_a_different_basket_and_selectable():
    assert set(ALPHA30_SEED) != set(ALPHA30_ENGINE)
    assert alpha30_symbols("seed") == ALPHA30_SEED
    assert [i.symbol for i in alpha30_instruments("seed")] == list(ALPHA30_SEED)
    with pytest.raises(ValueError, match="unknown alpha30 list"):
        alpha30_symbols("bogus")


def test_08_default_basket_matches_engine_and_seed_flag_switches():
    mod = _load("08_alpha30_union_ewr_backtest")
    ex = mod.Alpha30EWRExample()
    assert [i.symbol for i in ex.load_universe()] == list(ALPHA30_ENGINE)
    assert mod.BASKET == ALPHA30_ENGINE
    ex.parse_args(["--alpha30-list", "seed"])
    assert [i.symbol for i in ex.load_universe()] == list(ALPHA30_SEED)


def test_03_load_alpha30_variants_use_the_shared_lists():
    mod = _load("03_alpha30_constituents")
    assert [i.symbol for i in mod.load_alpha30()] == list(ALPHA30_ENGINE)
    assert [i.symbol for i in mod.load_alpha30(variant="seed")] == list(ALPHA30_SEED)
    assert mod.NIFTY200_ALPHA_30_SEED == ALPHA30_SEED


def test_banknifty_raises_clear_error_without_a_source(tmp_path):
    with pytest.raises(UniverseUnavailable, match="will not invent"):
        banknifty_members(data_dir=tmp_path)


def test_banknifty_reads_a_catalog_history_when_present(tmp_path):
    hist = UniverseHistory(
        "banknifty",
        (Constituent("AAA", dt.date(2020, 1, 1)), Constituent("BBB", dt.date(2020, 1, 1))),
    )
    save_universe_history(hist, data_dir=tmp_path)
    members = banknifty_members(data_dir=tmp_path)
    assert [m.symbol for m in members] == ["AAA", "BBB"]


def test_02_script_exits_with_message_not_traceback(capsys):
    mod = _load("02_banknifty_constituents")
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert "no Bank Nifty constituents" in str(exc.value)
