from __future__ import annotations

from honba_examples import settlement


class DummyMarket:
    def settlement_days_for(self, exchange: str) -> int:  # pragma: no cover - simple stub
        return 2


def test_cli_overrides_config_and_market():
    days, src = settlement.resolve_settlement_days("NSE", as_of=None, cli_value=1, cfg_value=2)
    assert days == 1
    assert src == "cli"


def test_config_overrides_market():
    days, src = settlement.resolve_settlement_days("NSE", as_of=None, cli_value=None, cfg_value=2)
    assert days == 2
    assert src == "config"


def test_market_default():
    days, src = settlement.resolve_settlement_days("NSE", as_of=None, cli_value=None, cfg_value=None)
    # market default is 1 for NSE equity in this codebase
    assert days == 1
    assert src == "market"


def test_precedence_chain():
    days, src = settlement.resolve_settlement_days("NSE", as_of=None, cli_value=0, cfg_value=3)
    assert days == 0
    assert src == "cli"
    days, src = settlement.resolve_settlement_days("NSE", as_of=None, cli_value=None, cfg_value=1)
    assert days == 1
    assert src == "config"


def test_cfg_value_none_uses_market():
    days, src = settlement.resolve_settlement_days("NSE", as_of=None, cli_value=None, cfg_value=None)
    assert days == 1
    assert src == "market"
