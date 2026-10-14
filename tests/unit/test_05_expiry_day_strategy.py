"""tests/unit/test_05_expiry_day_strategy.py: theta accounting of the expiry-day gamma scalp."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "options" / "05_expiry_day_strategy.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("expiry_day_strategy", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_theta_is_time_weighted_and_negative_for_long_options(example) -> None:
    """A long straddle pays theta; over a one-day session it cannot exceed the premium by 100x."""
    summary = example.run()["summary"]
    initial = example.run()["initial"]["total_premium"] * 15  # default lot size
    assert "theta_captured" not in summary
    assert summary["theta_pnl"] < 0.0
    # Daily theta integrated over one day is of the order of the premium, not steps x premium.
    assert abs(summary["theta_pnl"]) < 3.0 * initial


def test_theta_does_not_scale_with_step_count(example) -> None:
    coarse = example.run(intraday_steps=50, rebalance_threshold=10.0)["summary"]["theta_pnl"]
    fine = example.run(intraday_steps=200, rebalance_threshold=10.0)["summary"]["theta_pnl"]
    # No rebalancing (threshold 10): same position, same day; step count must not change it much.
    assert coarse == pytest.approx(fine, rel=0.5)
