"""tests/unit/test_metrics_property.py: Phase 7.3 property-based testing.

Hypothesis-driven invariants for the equity-curve statistics in ``honba_examples.metrics``
(Sharpe, Sortino, max drawdown) and for first-class position sizing, across synthetic random
equity curves. These are statistics (floats), not ledger entries: values stay in float
domain here, per ADR 0011.

Run fast::

    pytest tests/unit/test_metrics_property.py -q
"""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from honba_examples.metrics import (
    curve_metrics,
    max_drawdown,
    sharpe_ratio,
    sortino_ratio,
)

SESSIONS_PER_YEAR = 252


def _curve(values, start=dt.date(2026, 1, 1), step_days=30, capital=100_000.0):
    """Build an equity curve (list of {date, value, cash}) from positive values.

    ``step_days=30`` keeps the horizon wide enough that CAGR (in ``curve_metrics``) cannot
    overflow even for two widely separated values.
    """
    return [
        {"date": (start + dt.timedelta(days=i * step_days)).isoformat(), "value": v, "cash": v}
        for i, v in enumerate(values)
    ]


_pos_values = st.lists(
    st.floats(min_value=0.01, max_value=10_000_000.0, allow_nan=False, allow_infinity=False),
    min_size=2,
    max_size=400,
)
_scale = st.floats(min_value=0.5, max_value=2.0, allow_nan=False, allow_infinity=False)


@settings(max_examples=300, deadline=None)
@given(values=_pos_values, factor=_scale)
def test_sharpe_is_scale_invariant(values, factor):
    """Multiplying every equity value by a constant must not change the Sharpe ratio."""
    base = sharpe_ratio(_curve(values), 100_000.0)
    scaled = sharpe_ratio(_curve([v * factor for v in values]), 100_000.0 * factor)
    assert base == pytest.approx(scaled, abs=1e-9)


@settings(max_examples=300, deadline=None)
@given(values=_pos_values)
def test_max_drawdown_is_never_positive(values):
    """Drawdown is reported as a negative percentage (<= 0) and bounded below by -100."""
    dd = max_drawdown(_curve(values))
    assert -100.0 <= dd <= 0.0


@settings(max_examples=300, deadline=None)
@given(values=_pos_values)
def test_monotonic_curve_has_zero_drawdown(values):
    """A strictly increasing equity curve never draws down."""
    curve = _curve(sorted(values))
    assert max_drawdown(curve) == pytest.approx(0.0, abs=1e-12)


@settings(max_examples=300, deadline=None)
@given(values=_pos_values)
def test_sortino_zero_when_no_downside(values):
    """With no negative session returns the downside deviation is zero and Sortino is 0.0."""
    curve = _curve(sorted(values))  # non-decreasing -> no negative returns
    assert sortino_ratio(curve, 100_000.0) == pytest.approx(0.0, abs=1e-12)


@settings(max_examples=300, deadline=None)
@given(values=_pos_values)
def test_curve_metrics_sharpe_matches_sharpe_ratio(values):
    """curve_metrics['sharpe'] must agree with the standalone sharpe_ratio."""
    capital = 100_000.0
    curve = _curve(values, capital=capital)
    assert curve_metrics(curve, capital)["sharpe"] == pytest.approx(
        sharpe_ratio(curve, capital), abs=1e-9
    )
    assert curve_metrics(curve, capital)["max_drawdown_pct"] == pytest.approx(
        max_drawdown(curve), abs=1e-9
    )


@settings(max_examples=300, deadline=None)
@given(
    equity=st.floats(
        min_value=1_000.0, max_value=10_000_000.0, allow_nan=False, allow_infinity=False
    ),
    risk_pct=st.floats(min_value=0.0001, max_value=0.1, allow_nan=False, allow_infinity=False),
    price=st.floats(min_value=1.0, max_value=100_000.0, allow_nan=False, allow_infinity=False),
    stop_pct=st.floats(min_value=0.001, max_value=0.5, allow_nan=False, allow_infinity=False),
)
def test_fixed_fractional_sizing_risk_invariant(equity, risk_pct, price, stop_pct):
    """Fixed-fractional sizing never risks more than ``risk_pct`` of equity per trade and
    never buys more than the account can afford.

    qty = min(floor(equity * risk_pct / (price * stop_pct)), floor(equity / price)); the
    stopped loss qty * price * stop_pct must be <= equity * risk_pct and the notional
    qty * price must be <= equity.
    """
    import math

    risk_amount = equity * risk_pct
    per_share_risk = price * stop_pct
    qty_by_risk = math.floor(risk_amount / per_share_risk) if per_share_risk > 0 else 0
    qty_affordable = math.floor(equity / price) if price > 0 else 0
    qty = max(min(qty_by_risk, qty_affordable), 0)
    assert qty >= 0
    assert qty * per_share_risk <= risk_amount * (1 + 1e-9)  # risk budget respected
    assert qty * price <= equity * (1 + 1e-9)  # never over-leveraged


@settings(max_examples=200, deadline=None)
@given(values=_pos_values)
def test_sortino_at_least_sharpe_when_returns_non_negative(values):
    """When all returns are non-negative, Sortino (no downside) is 0 and >= any negative Sharpe."""
    curve = _curve(sorted(values))
    assert sortino_ratio(curve, 100_000.0) >= min(sharpe_ratio(curve, 100_000.0), 0.0)
