"""Integer paise at the money boundary (ADR 0011): exact, half away from zero, per-leg costs."""

from __future__ import annotations

import math

import pytest
from honba.domain.order import OrderSide
from honba.markets.india.costs import nse_equity_delivery_breakdown

from honba_examples.money import (
    delivery_cost_paise,
    notional_paise,
    paise_to_rupees,
    to_paise,
)


@pytest.mark.parametrize(
    ("rupees", "paise"),
    [
        (0.0, 0),
        (12.34, 1234),
        (1.005, 101),  # float 1.005 is 1.00499...; the decimal literal rounds half away
        (0.125, 13),
        (-0.125, -13),  # symmetric: half away from zero, not half-even
        (0.115, 12),
        (1_000_000.0, 100_000_000),
    ],
)
def test_to_paise_rounds_half_away_from_zero(rupees: float, paise: int) -> None:
    assert to_paise(rupees) == paise
    assert isinstance(to_paise(rupees), int)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_to_paise_rejects_non_finite(bad: float) -> None:
    with pytest.raises(ValueError):
        to_paise(bad)


def test_notional_is_exact_quantity_times_price() -> None:
    # 3 * 0.1 is 0.30000000000000004 in float; the ledger must say 30 paise.
    assert notional_paise(3, 0.1) == 30
    assert notional_paise(212, 15.42) == 326_904
    assert notional_paise(7, 341.005) == 238_704  # 2387.035 rupees -> half away


def test_paise_to_rupees_is_display_only_division() -> None:
    assert paise_to_rupees(12_345) == 123.45
    assert paise_to_rupees(-5) == -0.05


@pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
def test_delivery_cost_rounds_each_leg_before_summing(side: OrderSide) -> None:
    qty, px = 17, 186.10
    legs = nse_equity_delivery_breakdown(side, qty, px)
    expected = sum(
        to_paise(v)
        for v in (
            legs.brokerage,
            legs.stt,
            legs.exchange,
            legs.sebi,
            legs.ipft,
            legs.stamp_duty,
            legs.gst,
        )
    )
    got = delivery_cost_paise(side, qty, px)
    assert got == expected
    assert isinstance(got, int)
    assert got >= 0


def test_sell_costs_more_than_buy_because_of_stt() -> None:
    assert delivery_cost_paise(OrderSide.SELL, 100, 500.0) > delivery_cost_paise(
        OrderSide.BUY, 100, 500.0
    )
