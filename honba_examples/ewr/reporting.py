"""Trade diffing and summary printing for equal-weight simulations."""

from __future__ import annotations

from typing import Any

from honba_examples.ewr.model import SimResult
from honba_examples.output import (
    OutputOptions,
    print_data_notes,
    print_equity_summary,
    print_metrics,
    print_rebalance_schedule,
)


def trade_changes(trades: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    """Net share change per symbol from fills: ``(adds, sells)``, each ``{symbol: qty}``."""
    net: dict[str, int] = {}
    for t in trades:
        signed = t["qty"] if t["side"] == "buy" else -t["qty"]
        net[t["symbol"]] = net.get(t["symbol"], 0) + signed
    adds = {s: q for s, q in sorted(net.items()) if q > 0}
    sells = {s: -q for s, q in sorted(net.items()) if q < 0}
    return adds, sells


def format_changes(adds: dict[str, int], sells: dict[str, int]) -> str:
    """``adds [AAA+3, ZED+10]  sells [MID-5]``: symbols sorted, ``[]`` when empty."""
    add_text = ", ".join(f"{s}+{q}" for s, q in sorted(adds.items()))
    sell_text = ", ".join(f"{s}-{q}" for s, q in sorted(sells.items()))
    return f"adds [{add_text}]  sells [{sell_text}]"


def rebalance_changes(rebalances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per rebalance, from its fills. A settlement-delayed buy leg is folded
    into the rebalance whose sell leg funded it."""
    groups: list[dict[str, Any]] = []
    for r in rebalances:
        if r.get("leg") == "buy" and groups:
            groups[-1]["trades"].extend(r["trades"])
        else:
            groups.append({"date": r["date"], "trades": list(r["trades"])})
    out = []
    for g in groups:
        adds, sells = trade_changes(g["trades"])
        out.append({"date": g["date"], "adds": adds, "sells": sells})
    return out


def print_summary(result: SimResult, opts: OutputOptions | None = None) -> None:
    """Print the simulation summary through the shared example output presets."""
    opts = opts or OutputOptions()
    metrics = dict(result.metrics)
    metrics["traded_notional"] = sum(t["notional"] for r in result.rebalances for t in r["trades"])
    print_rebalance_schedule(result.rebalances, opts)
    print_metrics(metrics, opts)
    print_equity_summary(result.equity_curve, opts)
    print_data_notes(opts, missing_data=result.missing_data, never_held=result.never_held)
