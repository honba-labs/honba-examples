"""Fail-closed, reduce-only guard around ``honba.ai.decision`` for model-written decisions.

The core ``validate_llm_decision`` defaults a missing ``decision`` to "approve" and a missing
``decided_quantity`` to the original quantity, so an empty or malformed reply becomes an approval,
and it has no notion of protective exits. ``guard_llm_decision`` wraps it (the core is unchanged):

* A missing or invalid decision or quantity, a non-mapping reply, or any inconsistent field
  rejects the order (a VETO decision flagged ``fail_closed``) instead of approving it.
* A quantity increase is a hard error (``ValueError``, as in the core); only equal or lower
  quantities pass.
* Protective exits (stop orders, or any intent the caller marks ``reduce_only``) are never
  cancelled or shrunk by the model: a veto/size-down is ignored and flagged with a reason, and an
  unusable reply keeps the exit. A model must not leave a position larger than the strategy meant.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from honba.ai.decision import DecisionKind, LlmDecision, validate_llm_decision
from honba.domain.order import OrderIntent, OrderType

__all__ = ["GuardedDecision", "guard_llm_decision", "is_protective_exit"]

_PROTECTIVE_TYPES = frozenset(
    {OrderType.STOP_MARKET, OrderType.STOP_LIMIT, OrderType.TRAILING_STOP}
)
_EPS = 1e-9


@dataclass(frozen=True, slots=True)
class GuardedDecision:
    """Outcome of ``guard_llm_decision``.

    ``effective_intent`` is what to actually submit (``None`` = cancelled). ``fail_closed`` is
    true when the reply was unusable; ``overridden`` is true when the model's decision was not
    honoured to protect an exit; ``reasons`` explains either.
    """

    decision: LlmDecision
    effective_intent: OrderIntent | None
    fail_closed: bool
    overridden: bool
    reasons: tuple[str, ...]


def is_protective_exit(intent: OrderIntent, *, reduce_only: bool = False) -> bool:
    """True for stop-type orders or when the caller flags the intent ``reduce_only``."""
    return reduce_only or intent.order_type in _PROTECTIVE_TYPES


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _kind(value: Any) -> DecisionKind | None:
    if isinstance(value, DecisionKind):
        return value
    try:
        return DecisionKind(value.lower()) if isinstance(value, str) else None
    except ValueError:
        return None


def _problem(raw: Any) -> str | None:
    """Why ``raw`` is not an explicit, well-formed decision, or None."""
    if not isinstance(raw, Mapping):
        return f"reply is not a mapping ({type(raw).__name__})"
    decision = _kind(raw.get("decision"))
    if decision is None:
        return f"missing or unknown decision {raw.get('decision')!r}"
    qty = raw.get("decided_quantity")
    if decision is DecisionKind.VETO:
        if qty is not None and not (_is_number(qty) and abs(qty) <= _EPS):
            return f"veto with non-zero decided_quantity {qty!r}"
    elif not _is_number(qty):
        return f"missing or non-numeric decided_quantity {qty!r}"
    return None


def _rejection(intent: OrderIntent, reason: str) -> LlmDecision:
    return LlmDecision(
        order_id="unparsed",
        symbol=intent.instrument_id.symbol,
        side=intent.side,
        original_quantity=intent.quantity,
        decision=DecisionKind.VETO,
        decided_quantity=0.0,
        rationale=f"fail-closed: {reason}",
    )


def guard_llm_decision(
    raw: Mapping[str, Any],
    original_intent: OrderIntent,
    *,
    reduce_only: bool = False,
) -> GuardedDecision:
    """Validate ``raw`` against ``original_intent`` and decide what may be submitted.

    Raises:
        ValueError: the model tried to increase the quantity (a hard violation, as in the core).
    """
    protective = is_protective_exit(original_intent, reduce_only=reduce_only)
    reasons: list[str] = []

    problem = _problem(raw)
    decision: LlmDecision | None = None
    if problem is None:
        explicit = dict(raw)
        if explicit.get("decided_quantity") is None and _kind(explicit["decision"]) is (
            DecisionKind.VETO
        ):
            explicit["decided_quantity"] = 0.0
        try:
            decision = validate_llm_decision(explicit, original_intent=original_intent)
        except (ValueError, TypeError) as exc:
            qty = explicit.get("decided_quantity")
            if isinstance(qty, (int, float)) and qty > original_intent.quantity + _EPS:
                raise
            problem = str(exc)
    if decision is None:
        assert problem is not None
        decision = _rejection(original_intent, problem)
        reasons.append(f"unusable model reply: {problem}")
        if protective:
            reasons.append("protective exit kept as submitted")
            return GuardedDecision(decision, original_intent, True, True, tuple(reasons))
        return GuardedDecision(decision, None, True, False, tuple(reasons))

    if protective and decision.decision is not DecisionKind.APPROVE:
        reasons.append(
            f"{decision.decision.value} ignored: protective exit "
            "(stop order or reduce_only) must not be cancelled or shrunk"
        )
        return GuardedDecision(decision, original_intent, False, True, tuple(reasons))

    effective = (
        None
        if decision.decision is DecisionKind.VETO
        else dataclasses.replace(original_intent, quantity=decision.decided_quantity)
    )
    return GuardedDecision(decision, effective, False, False, tuple(reasons))
