"""decision_guard: fail-closed parsing, reduce-only quantity, protective exits keep working."""

from __future__ import annotations

import math

import pytest
from honba.ai.decision import DecisionKind
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent

from honba_examples.decision_guard import guard_llm_decision, is_protective_exit

SYM = InstrumentId("NIFTY", "NSE")
BUY = OrderIntent.market_buy(SYM, 100.0)
STOP_SELL = OrderIntent.stop_sell(SYM, 100.0, 95.0)


def _raw(**kw):
    base = {"order_id": "o1", "decision": "approve", "decided_quantity": 100.0, "rationale": "ok"}
    base.update(kw)
    return base


def test_explicit_approve_passes_intent_through():
    g = guard_llm_decision(_raw(), BUY)
    assert g.decision.decision is DecisionKind.APPROVE
    assert g.effective_intent == BUY
    assert not g.fail_closed and not g.overridden


def test_size_down_reduces_quantity():
    g = guard_llm_decision(_raw(decision="size_down", decided_quantity=40.0), BUY)
    assert g.effective_intent is not None and g.effective_intent.quantity == 40.0


def test_veto_without_a_quantity_is_still_a_veto():
    g = guard_llm_decision({"order_id": "o", "decision": "veto"}, BUY)
    assert g.effective_intent is None and not g.fail_closed


def test_veto_cancels_an_entry():
    g = guard_llm_decision(_raw(decision="veto", decided_quantity=0.0), BUY)
    assert g.effective_intent is None and not g.overridden


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"order_id": "o1"},
        {"decision": None},
        {"decision": ""},
        {"decision": "maybe"},
        {"decision": 7},
        {"decision": "approve"},  # quantity missing
        {"decision": "size_down"},  # quantity missing
        {"decision": "approve", "decided_quantity": None},
        {"decision": "approve", "decided_quantity": "100"},
        {"decision": "approve", "decided_quantity": float("nan")},
        {"decision": "size_down", "decided_quantity": math.inf},
        {"decision": "approve", "decided_quantity": True},
        {"decision": "size_down", "decided_quantity": 100.0},  # not a reduction
        {"decision": "size_down", "decided_quantity": -5.0},
        {"decision": "approve", "decided_quantity": 100.0, "confidence": 7.0},
    ],
)
def test_missing_or_invalid_reply_rejects_an_entry(raw):
    g = guard_llm_decision(raw, BUY)
    assert g.fail_closed
    assert g.effective_intent is None
    assert g.decision.decision is DecisionKind.VETO
    assert g.reasons


@pytest.mark.parametrize("raw", [None, [], "approve", 3])
def test_non_mapping_reply_rejects(raw):
    g = guard_llm_decision(raw, BUY)  # type: ignore[arg-type]
    assert g.fail_closed and g.effective_intent is None


@pytest.mark.parametrize("qty", [100.5, 150.0])
def test_quantity_increase_raises(qty):
    with pytest.raises(ValueError, match="cannot increase"):
        guard_llm_decision(_raw(decision="size_down", decided_quantity=qty), BUY)


def test_protective_detection():
    assert is_protective_exit(STOP_SELL)
    assert is_protective_exit(BUY, reduce_only=True)
    assert not is_protective_exit(BUY)


@pytest.mark.parametrize(
    "raw",
    [
        _raw(decision="veto", decided_quantity=0.0),
        _raw(decision="size_down", decided_quantity=10.0),
    ],
)
def test_veto_or_size_down_of_protective_exit_is_ignored_with_reason(raw):
    g = guard_llm_decision(raw, STOP_SELL)
    assert g.overridden
    assert g.effective_intent == STOP_SELL
    assert any("protective" in r for r in g.reasons)
    # reduce_only flag works for plain market exits too
    exit_sell = OrderIntent.market_sell(SYM, 100.0)
    g2 = guard_llm_decision(raw, exit_sell, reduce_only=True)
    assert g2.effective_intent == exit_sell and g2.overridden


def test_garbage_reply_on_protective_exit_keeps_the_exit():
    g = guard_llm_decision({}, STOP_SELL)
    assert g.fail_closed
    assert g.effective_intent == STOP_SELL  # failing closed must not cancel a stop-loss
    assert g.overridden


def test_approve_of_protective_exit_is_untouched():
    g = guard_llm_decision(_raw(), STOP_SELL)
    assert g.effective_intent == STOP_SELL and not g.overridden


def test_09_demo_runs_and_exercises_the_guard(capsys):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "ai_research" / "09_typed_llm_decision.py"
    spec = importlib.util.spec_from_file_location("ex09_demo", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.run_typed_llm_decision_demo()
    out = capsys.readouterr().out
    assert "Fail-closed parsing" in out and "veto ignored" in out
