#!/usr/bin/env python3
"""Demonstration of Typed LLM Decisions (E5-S7).

Demonstrates:
1. Strategy order intent creation.
2. Veto decision (rejects intent upon adverse event detection).
3. Size-down decision (scales down quantity upon elevated risk).
4. Safety invariant enforcement: ``schema_rejects_increase`` forbids any size increase.
5. Serialization to Journal v1 NDJSON audit records (``decision_in_journal``).
"""

from __future__ import annotations

from honba.ai.decision import (
    DecisionKind,
    LlmDecision,
    apply_llm_decision,
    validate_llm_decision,
)
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderSide
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

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
        validate_llm_decision(
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

    # 5. Journal v1 Audit Log Records
    console.print(
        "\n[bold yellow]5. Decision in Journal (Journal v1 NDJSON records):[/bold yellow]"
    )
    table = Table(title="Journal v1 Records", show_lines=True)
    table.add_column("Type", style="cyan")
    table.add_column("Order ID", style="magenta")
    table.add_column("Decision", style="bold")
    table.add_column("Qty Move", style="green")
    table.add_column("Rationale", style="white")

    records = [
        size_down_dec.to_journal_record(),
        veto_dec.to_journal_record(),
    ]

    for rec in records:
        table.add_row(
            rec["type"],
            rec["order_id"],
            rec["decision"].upper(),
            f"{rec['original_qty']:.0f} -> {rec['decided_qty']:.0f}",
            rec["rationale"],
        )
    console.print(table)
    console.print(
        "[bold green]All typed LLM decision invariants verified successfully![/bold green]"
    )


if __name__ == "__main__":
    run_typed_llm_decision_demo()
