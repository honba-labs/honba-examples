#!/usr/bin/env python3
"""Demonstration of Typed LLM Decisions (E5-S7).

Demonstrates:
1. Strategy order intent creation.
2. Veto decision (rejects intent upon adverse event detection).
3. Size-down decision (scales down quantity upon elevated risk).
4. Safety invariant enforcement: ``schema_rejects_increase`` forbids any size increase.
5. Fail-closed parsing: an empty or malformed model reply rejects the order, not approves it.
6. Reduce-only: a veto of a stop-loss exit is ignored and flagged.
7. Serialization to Journal v1 NDJSON audit records (``decision_in_journal``).

Pitfalls::

    - No model is called: the decisions are built by hand with invented rationales and
      confidences (0.88, 0.99), so nothing here shows a model's vetoes add value. In a backtest a
      real model may know what happened after the decision date; test vetoes on dates after its
      training cutoff or replay recorded live decisions.
    - The core invariant bounds quantity, not exposure: validate_llm_decision itself accepts a
      veto or size-down on an exit. guard_llm_decision (honba_examples/decision_guard.py) adds the
      reduce-only rule, but only for intents it can recognise: stop-type orders, or any intent you
      pass reduce_only=True for. A plain market SELL that is really a stop-out is not detected
      unless you flag it. Route risk-reducing orders around the model when in doubt.
    - Fail-closed parsing: guard_llm_decision rejects the order when the decision or
      decided_quantity is missing or malformed (the core would default to "approve" and the
      original quantity). Rejecting an entry is safe; for a protective exit it keeps the exit and
      flags the override, and a quantity increase raises ValueError.
    - confidence and rationale are model-written text that is stored for audit and never used to
      gate the order. Treat the rationale as untrusted (escape it, do not feed it back into a
      prompt unchecked), and do not read confidence as a calibrated probability.
    - Non-determinism: the Journal v1 record is what makes a run replayable. Replay from the
      journal rather than querying the model again, and keep one record per decision.
"""

from __future__ import annotations

from honba.ai.decision import DecisionKind, LlmDecision, apply_llm_decision
from honba.display import Column, render_table
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderSide
from rich.console import Console
from rich.panel import Panel

from honba_examples.decision_guard import guard_llm_decision
from honba_examples.display_style import SECONDARY

console = Console()


def run_typed_llm_decision_demo() -> None:
    console.print(
        Panel.fit(
            "[bold cyan]Honba E5-S7: Typed LLM Decisions & Safety Invariants[/bold cyan]\n"
            "[dim]Strict veto / size-down enforcement: an AI model can veto or reduce risk, "
            "but never increase exposure.[/dim]",
            border_style="cyan",
        )
    )

    # 1. Proposed Strategy Intent
    sym_nifty = InstrumentId("NIFTY", "NSE")
    intent = OrderIntent.market_buy(sym_nifty, 100.0)
    console.print(
        f"\n[bold yellow]1. Strategy proposed intent:[/bold yellow] "
        f"[green]{intent.side.value.upper()}[/green] {intent.quantity:.0f}x {intent.instrument_id.symbol}"
    )

    # 2. Decision: Size Down
    size_down_dec = LlmDecision(
        order_id="nifty-intent-001",
        symbol="NIFTY",
        side=OrderSide.BUY,
        original_quantity=100.0,
        decision=DecisionKind.SIZE_DOWN,
        decided_quantity=40.0,
        rationale="Elevated intraday implied volatility; sizing down allocation by 60%",
        confidence=0.88,
    )
    sized_intent = apply_llm_decision(intent, size_down_dec)
    assert sized_intent is not None
    console.print(
        f"[bold green]2. Size-down applied:[/bold green] "
        f"Original: {size_down_dec.original_quantity:.0f} -> Decided: {sized_intent.quantity:.0f} "
        f"[dim]({size_down_dec.rationale})[/dim]"
    )

    # 3. Decision: Veto
    veto_dec = LlmDecision(
        order_id="nifty-intent-001",
        symbol="NIFTY",
        side=OrderSide.BUY,
        original_quantity=100.0,
        decision=DecisionKind.VETO,
        decided_quantity=0.0,
        rationale="Critical macro CPI print miss announced; vetoing order",
        confidence=0.99,
    )
    vetoed_intent = apply_llm_decision(intent, veto_dec)
    assert vetoed_intent is None
    console.print(
        f"[bold red]3. Veto applied:[/bold red] "
        f"Order cancelled completely (intent -> None) [dim]({veto_dec.rationale})[/dim]"
    )

    # 4. Invariant: Schema Rejects Increase
    console.print("\n[bold yellow]4. Invariant Check (schema_rejects_increase):[/bold yellow]")
    try:
        guard_llm_decision(
            {
                "order_id": "nifty-intent-001",
                "decision": "size_down",
                "decided_quantity": 150.0,  # Hallucinated increase!
                "rationale": "High conviction breakout, attempting to supersize",
            },
            original_intent=intent,
        )
        console.print("[red]FAIL: Increase was erroneously permitted![/red]")
    except ValueError as e:
        console.print(
            f"[bold green]PASS:[/bold green] Hallucinated increase safely rejected: [red]{e}[/red]"
        )

    # 5. Fail closed: an empty / malformed reply must not become an approval
    console.print("\n[bold yellow]5. Fail-closed parsing (empty reply):[/bold yellow]")
    empty = guard_llm_decision({}, intent)
    assert empty.fail_closed and empty.effective_intent is None
    console.print(
        f"[bold green]PASS:[/bold green] empty reply rejects the order "
        f"[dim]({'; '.join(empty.reasons)})[/dim]"
    )

    # 6. Reduce-only: the model cannot cancel or shrink a protective exit
    console.print("\n[bold yellow]6. Protective exit (stop-loss SELL):[/bold yellow]")
    stop_loss = OrderIntent.stop_sell(sym_nifty, 100.0, 95.0)
    guarded = guard_llm_decision(
        {"order_id": "nifty-stop-001", "decision": "veto", "rationale": "ignore the stop"},
        stop_loss,
    )
    assert guarded.overridden and guarded.effective_intent == stop_loss
    console.print(
        f"[bold green]PASS:[/bold green] veto ignored, stop-loss still submitted "
        f"[dim]({'; '.join(guarded.reasons)})[/dim]"
    )

    # 7. Journal v1 Audit Log Records
    console.print(
        "\n[bold yellow]7. Decision in Journal (Journal v1 NDJSON records):[/bold yellow]"
    )
    records = [
        size_down_dec.to_journal_record(),
        veto_dec.to_journal_record(),
    ]
    columns = [
        Column("type", "Type", style="cyan"),
        Column("order_id", "Order ID", style="magenta"),
        Column("decision", "Decision", fmt=lambda v: v.upper(), style="bold"),
        Column(
            "qty_move",
            "Qty Move",
            style="green",
        ),
        Column("rationale", "Rationale", style=SECONDARY),
    ]
    rows = [
        {**rec, "qty_move": f"{rec['original_qty']:.0f} -> {rec['decided_qty']:.0f}"}
        for rec in records
    ]
    render_table(rows, columns, title="Journal v1 Records")
    console.print(
        "[bold green]All typed LLM decision invariants verified successfully![/bold green]"
    )


if __name__ == "__main__":
    run_typed_llm_decision_demo()
