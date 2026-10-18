"""tests/unit/test_basic_order_guard.py: order-placing basics refuse real brokers by default."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from honba.adapters.errors import AdapterNotFound
from honba.domain.instrument import InstrumentId

from honba_examples.cli import ALLOW_LIVE_ORDERS_ENV, require_paper_adapter

ROOT = Path(__file__).resolve().parents[2] / "examples"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "basic" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fake_is_always_allowed(monkeypatch) -> None:
    monkeypatch.delenv(ALLOW_LIVE_ORDERS_ENV, raising=False)
    require_paper_adapter("fake")


def test_a_real_broker_is_refused_without_the_opt_in(monkeypatch) -> None:
    monkeypatch.delenv(ALLOW_LIVE_ORDERS_ENV, raising=False)
    with pytest.raises(ValueError, match=ALLOW_LIVE_ORDERS_ENV):
        require_paper_adapter("dhan")


def test_the_opt_in_allows_it(monkeypatch) -> None:
    monkeypatch.setenv(ALLOW_LIVE_ORDERS_ENV, "1")
    require_paper_adapter("dhan")


@pytest.mark.parametrize("name", ["04_place_order_paper", "05_check_positions"])
def test_scripts_refuse_before_resolving_the_adapter(name, monkeypatch) -> None:
    monkeypatch.delenv(ALLOW_LIVE_ORDERS_ENV, raising=False)
    mod = _load(name)
    with pytest.raises(ValueError, match=ALLOW_LIVE_ORDERS_ENV):
        mod.run("not-a-registered-broker")


@pytest.mark.parametrize("name", ["04_place_order_paper", "05_check_positions"])
def test_with_the_opt_in_the_normal_adapter_lookup_runs(name, monkeypatch) -> None:
    monkeypatch.setenv(ALLOW_LIVE_ORDERS_ENV, "1")
    mod = _load(name)
    with pytest.raises(AdapterNotFound):
        mod.run("not-a-registered-broker")


def test_position_after_buy_is_the_bought_instrument_not_the_first_row() -> None:
    mod = _load("05_check_positions")

    class Held:
        def __init__(self, instrument_id: InstrumentId) -> None:
            self.instrument_id = instrument_id

    positions = [Held(InstrumentId("INFY", "NSE")), Held(mod._RELIANCE)]
    assert mod._position_of(positions, mod._RELIANCE) is positions[1]
    assert mod._position_of(positions[:1], mod._RELIANCE) is None
