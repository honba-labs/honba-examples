"""One place every example prints through: presets over ``honba.display``.

Examples hand plain data (dicts, the fields of ``BacktestRun`` / ``SimResult``) to these
functions; layout, number formats, ``--format table|json|csv|plain`` and ``--width`` live here.

    parser = argparse.ArgumentParser()
    add_output_args(parser)
    opts = resolve_output(parser.parse_args())
    print_run_header({...}, opts)
    print_metrics(result.metrics, opts)

Machine formats emit one ``{"block": name, "rows": [...]}`` JSON document per call (stable keys,
raw values); ``csv`` emits the block's rows with a header row.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TextIO

from honba.display import (
    Column,
    OutputFormat,
    format_percent,
    render_kv,
    render_table,
)
from honba.display import render as _render

__all__ = [
    "OutputFormat",
    "OutputOptions",
    "add_output_args",
    "print_changes",
    "print_data_notes",
    "print_equity_summary",
    "print_holdings",
    "print_metrics",
    "print_rebalance_schedule",
    "print_rejections",
    "print_run_header",
    "resolve_output",
]


@dataclass
class OutputOptions:
    """Where and how an example prints. ``out=None`` means the current ``sys.stdout``."""

    fmt: OutputFormat = OutputFormat.TABLE
    width: int | None = None
    out: TextIO | None = field(default=None, repr=False)


def add_output_args(parser: argparse.ArgumentParser) -> None:
    """Add the shared ``--format`` and ``--width`` flags."""
    parser.add_argument(
        "--format",
        dest="output_format",
        type=_parse_format,
        default=OutputFormat.TABLE,
        help="Output format: table, json, csv, plain (default: table)",
    )
    parser.add_argument(
        "--width", dest="output_width", type=int, default=None, help="Fixed output width"
    )


def _parse_format(value: str) -> OutputFormat:
    try:
        return OutputFormat.parse(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def resolve_output(args: argparse.Namespace | Mapping[str, Any] | None = None) -> OutputOptions:
    """Build ``OutputOptions`` from parsed arguments (defaults when absent)."""
    get = args.get if isinstance(args, Mapping) else lambda k, d=None: getattr(args, k, d)
    fmt = OutputFormat.parse(get("output_format", OutputFormat.TABLE) or OutputFormat.TABLE)
    return OutputOptions(fmt=fmt, width=get("output_width", None))


# ---------------------------------------------------------------------------
# Number formats
# ---------------------------------------------------------------------------


def _num(v: Any, d: int = 2) -> str:
    try:
        return f"{float(v):,.{d}f}"
    except (TypeError, ValueError):
        return str(v)


def _signed(v: Any) -> str:
    return f"{int(v):+d}"


def _stream(opts: OutputOptions) -> TextIO:
    return opts.out if opts.out is not None else sys.stdout


def _machine(opts: OutputOptions) -> bool:
    return opts.fmt in (OutputFormat.JSON, OutputFormat.CSV)


def _emit_machine(
    block: str, keys: Sequence[str], rows: Sequence[Mapping[str, Any]], opts: OutputOptions
) -> None:
    out = _stream(opts)
    if opts.fmt is OutputFormat.JSON:
        print(json.dumps({"block": block, "rows": list(rows)}, indent=2, default=str), file=out)
    else:
        _render(rows, list(keys), OutputFormat.CSV, out=out)


def _table(
    block: str,
    title: str | None,
    columns: Sequence[Column],
    rows: Sequence[Mapping[str, Any]],
    opts: OutputOptions,
    *,
    raw: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    """Emit ``rows`` (display cells) or, for machine formats, ``raw`` (or ``rows``)."""
    if _machine(opts):
        _emit_machine(block, [c.key for c in columns], raw if raw is not None else rows, opts)
        return
    render_table(
        rows,
        columns,
        title=title,
        out=_stream(opts),
        width=opts.width,
        plain=opts.fmt is OutputFormat.PLAIN,
    )


def _kv(
    block: str,
    title: str,
    pairs: Sequence[tuple[str, Any]],
    raw: Mapping[str, Any],
    o: OutputOptions,
) -> None:
    if _machine(o):
        _emit_machine(block, list(raw), [dict(raw)], o)
        return
    render_kv(pairs, title=title, out=_stream(o), width=o.width, plain=o.fmt is OutputFormat.PLAIN)


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


def print_run_header(info: Mapping[str, Any], opts: OutputOptions | None = None) -> None:
    """Run header: strategy, universe, dates, capital, settlement, backend (missing keys skipped).

    Any additional keys in ``info`` not in the fixed set are appended in insertion order.
    """
    opts = opts or OutputOptions()
    pairs: list[tuple[str, Any]] = []
    for key, label in (("strategy", "Strategy"), ("universe", "Universe")):
        if info.get(key) is not None:
            pairs.append((label, info[key]))
    if info.get("start") is not None or info.get("end") is not None:
        pairs.append(("Period", f"{info.get('start', '?')} -> {info.get('end', '?')}"))
    if info.get("capital") is not None:
        pairs.append(("Capital", _num(info["capital"])))
    if info.get("settlement_days") is not None:
        pairs.append(("Settlement", f"T+{info['settlement_days']}"))
    if info.get("backend") is not None:
        pairs.append(("Backend", info["backend"]))
    fixed_keys = {"strategy", "universe", "start", "end", "capital", "settlement_days", "backend"}
    for key, value in info.items():
        if key not in fixed_keys and value is not None:
            label = " ".join(w.capitalize() for w in key.split("_"))
            pairs.append((label, value))
    _kv("run_header", "Run", pairs, dict(info), opts)


_METRIC_ORDER = (
    "final_value",
    "final_equity",
    "total_return_pct",
    "cagr_pct",
    "max_drawdown_pct",
    "sharpe",
    "total_fees",
    "turnover",
    "traded_notional",
    "avg_cash_pct",
    "n_fills",
    "n_rebalances",
    "initial_corpus",
    "total_invested",
    "net_profit",
    "sips_executed",
)


def _metric_text(key: str, v: Any) -> str:
    if key in ("total_return_pct", "cagr_pct", "max_drawdown_pct"):
        return format_percent(v)
    if key in ("sharpe", "turnover"):
        return _num(v, 3)
    if key in ("sips_executed",) or key.startswith("n_"):
        return _num(v, 0)
    return _num(v)


_METRIC_LABEL_OVERRIDES = {
    "n_fills": "Fills",
    "n_trades": "Trades",
    "n_rejections": "Rejections",
    "n_suppressed_warmup": "Suppressed (warm-up)",
    "n_released_unfunded": "Buys cut for cash",
    "n_unfilled_at_end": "Unfilled at end",
    "n_released_no_position": "Released (no position)",
    "n_rejected_intents": "Rejected intents",
    "sips_executed": "SIP installments",
    "total_invested": "Total invested",
    "initial_corpus": "Initial corpus",
    "net_profit": "Net profit",
}


def _metric_label(key: str) -> str:
    if key in _METRIC_LABEL_OVERRIDES:
        return _METRIC_LABEL_OVERRIDES[key]
    label = key.replace("_pct", " %").replace("_", " ").capitalize()
    return label.replace("Cagr", "CAGR")


def print_metrics(metrics: Mapping[str, Any], opts: OutputOptions | None = None) -> None:
    """Metrics block: known metrics first in a fixed order, then the rest sorted."""
    opts = opts or OutputOptions()
    keys = [k for k in _METRIC_ORDER if k in metrics]
    keys += sorted(k for k in metrics if k not in _METRIC_ORDER)
    text = [{"metric": _metric_label(k), "value": _metric_text(k, metrics[k])} for k in keys]
    raw = [{"metric": k, "value": metrics[k]} for k in keys]
    cols = [Column("metric", "Metric"), Column("value", "Value", align="right")]
    _table("metrics", "Metrics", cols, text, opts, raw=raw)


def print_changes(
    rebalances: Sequence[Mapping[str, Any]], opts: OutputOptions | None = None
) -> None:
    """Per-rebalance changes: net shares per (date, symbol) with price, notional and cost.

    ``rebalances`` items carry ``date`` and ``trades`` (``symbol, side, qty, price, notional, fee``).
    """
    opts = opts or OutputOptions()
    raw: list[dict[str, Any]] = []
    for r in rebalances:
        agg: dict[str, dict[str, float]] = {}
        for t in r.get("trades", []):
            a = agg.setdefault(t["symbol"], {"net": 0, "qty": 0, "notional": 0.0, "cost": 0.0})
            a["net"] += t["qty"] if t["side"] == "buy" else -t["qty"]
            a["qty"] += t["qty"]
            a["notional"] += t["notional"]
            a["cost"] += t.get("fee", 0.0)
        for sym in sorted(agg):
            a = agg[sym]
            raw.append(
                {
                    "date": r["date"],
                    "symbol": sym,
                    "side": "BUY" if a["net"] > 0 else "SELL" if a["net"] < 0 else "FLAT",
                    "net_shares": int(a["net"]),
                    "price": a["notional"] / a["qty"] if a["qty"] else 0.0,
                    "notional": a["notional"],
                    "cost": a["cost"],
                }
            )
    cols = [
        Column("date", "Date"),
        Column("symbol", "Symbol"),
        Column("side", "Side"),
        Column("net_shares", "Net shares", fmt=_signed),
        Column("price", "Price", fmt=_num),
        Column("notional", "Notional", fmt=_num),
        Column("cost", "Cost", fmt=_num),
    ]
    _table("changes", "Changes per rebalance", cols, raw, opts)


def print_rebalance_schedule(
    rebalances: Sequence[Mapping[str, Any]], opts: OutputOptions | None = None
) -> None:
    """Concise rebalance schedule table: 1 row per rebalance session.

    Categorizes trades into Added (new position), Removed (exit), and Rebalanced (trim/top-up),
    with Day 0 summarized compactly as the Initial basket funding.
    """
    opts = opts or OutputOptions()
    groups: list[dict[str, Any]] = []
    for r in rebalances:
        if r.get("leg") == "buy" and groups:
            groups[-1]["trades"].extend(r.get("trades", []))
            if "sip_injected" in r:
                groups[-1]["sip_injected"] = groups[-1].get("sip_injected", 0.0) + r["sip_injected"]
        else:
            groups.append(
                {
                    "date": r["date"],
                    "trades": list(r.get("trades", [])),
                    "sip_injected": r.get("sip_injected", 0.0),
                }
            )

    held: dict[str, int] = {}
    raw: list[dict[str, Any]] = []

    for idx, g in enumerate(groups):
        trades = g["trades"]
        notional = sum(t.get("notional", 0.0) for t in trades)
        net: dict[str, int] = {}
        for t in trades:
            signed = t["qty"] if t.get("side") == "buy" else -t["qty"]
            net[t["symbol"]] = net.get(t["symbol"], 0) + signed

        added: list[str] = []
        removed: list[str] = []
        rebalanced_items: list[tuple[int, str]] = []

        for sym in sorted(net):
            delta = net[sym]
            if delta == 0:
                continue
            before = held.get(sym, 0)
            after = before + delta
            if before == 0 and after > 0:
                added.append(f"+{sym}({delta})")
            elif before > 0 and after == 0:
                removed.append(f"-{sym}(all)")
            elif before > 0 and after > 0:
                sign = "+" if delta > 0 else "-"
                rebalanced_items.append((abs(delta), f"{sign}{sym}({abs(delta)})"))
            held[sym] = after
            if after == 0:
                held.pop(sym, None)

        rebalanced_items.sort(key=lambda x: -x[0])
        rebalanced = [item[1] for item in rebalanced_items]

        if idx == 0:
            event = "Initial"
            if len(added) >= 10:
                added_str = f"{len(added)} basket names"
            elif added:
                added_str = ", ".join(added)
            else:
                added_str = "—"
            removed_str = "—"
            rebalanced_str = "—"
        else:
            sip_inj = g.get("sip_injected", 0.0)
            event = "Rebal + SIP" if sip_inj > 0 else "Rebalance"
            added_str = ", ".join(added) if added else "—"
            removed_str = ", ".join(removed) if removed else "—"
            if len(rebalanced) > 4:
                rebalanced_str = f"{', '.join(rebalanced[:4])} (+{len(rebalanced) - 4} more)"
            elif rebalanced:
                rebalanced_str = ", ".join(rebalanced)
            else:
                rebalanced_str = "—"

        raw.append(
            {
                "date": g["date"],
                "event": event,
                "added": added_str,
                "removed": removed_str,
                "rebalanced": rebalanced_str,
                "notional": notional,
            }
        )

    cols = [
        Column("date", "Date"),
        Column("event", "Event"),
        Column("added", "Added (New)"),
        Column("removed", "Removed (Exit)"),
        Column("rebalanced", "Rebalanced (Trim / Top-up)"),
        Column("notional", "Notional", align="right", fmt=_num),
    ]
    _table("schedule", "Rebalance Schedule", cols, raw, opts)


def print_holdings(
    holdings: Mapping[str, int],
    opts: OutputOptions | None = None,
    *,
    prices: Mapping[str, float] | None = None,
) -> None:
    """Holdings table; with ``prices`` also price and market value."""
    opts = opts or OutputOptions()
    raw = []
    for sym in sorted(holdings):
        row: dict[str, Any] = {"symbol": sym, "shares": holdings[sym]}
        if prices is not None:
            px = prices.get(sym)
            row["price"] = px
            row["value"] = None if px is None else px * holdings[sym]
        raw.append(row)
    cols = [Column("symbol", "Symbol"), Column("shares", "Shares")]
    if prices is not None:
        cols += [Column("price", "Price", fmt=_num), Column("value", "Value", fmt=_num)]
    _table("holdings", "Final holdings", cols, raw, opts)


def print_rejections(
    rejections: Sequence[Mapping[str, Any]], opts: OutputOptions | None = None
) -> None:
    """Rejected or unfilled orders: date, symbol, side, quantity, reason."""
    opts = opts or OutputOptions()
    keys = ("date", "symbol", "side", "quantity", "reason")
    raw = [{k: r.get(k) for k in keys} for r in rejections]
    cols = [
        Column("date", "Date"),
        Column("symbol", "Symbol"),
        Column("side", "Side"),
        Column("quantity", "Quantity"),
        Column("reason", "Reason"),
    ]
    _table("rejections", "Rejections", cols, raw, opts)


def print_data_notes(
    opts: OutputOptions | None = None,
    *,
    missing_data: Sequence[str] = (),
    never_held: Sequence[str] = (),
) -> None:
    """Missing-data and never-held notes; prints nothing when both are empty."""
    opts = opts or OutputOptions()
    if not missing_data and not never_held:
        return
    if _machine(opts):
        _emit_machine(
            "notes",
            ["missing_data", "never_held"],
            [{"missing_data": list(missing_data), "never_held": list(never_held)}],
            opts,
        )
        return
    out = _stream(opts)
    if missing_data:
        print(
            f"\nNo Parquet bars for {len(missing_data)} basket members: {', '.join(missing_data)}",
            file=out,
        )
    if never_held:
        print(
            f"\nNever held (1 share costs more than its target): {', '.join(never_held)}", file=out
        )


def print_equity_summary(
    curve: Sequence[Mapping[str, Any]], opts: OutputOptions | None = None
) -> None:
    """Equity-curve summary: sessions, first/last value, peak and trough with dates, avg cash %."""
    opts = opts or OutputOptions()
    if not curve:
        _kv("equity_summary", "Equity curve", [("Sessions", 0)], {"sessions": 0}, opts)
        return
    peak = max(curve, key=lambda p: p["value"])
    trough = min(curve, key=lambda p: p["value"])
    cash_pct = [
        100.0 * p["cash"] / p["value"] for p in curve if p.get("cash") is not None and p["value"]
    ]
    raw = {
        "sessions": len(curve),
        "first_date": curve[0]["date"],
        "last_date": curve[-1]["date"],
        "start_value": curve[0]["value"],
        "end_value": curve[-1]["value"],
        "peak_value": peak["value"],
        "peak_date": peak["date"],
        "trough_value": trough["value"],
        "trough_date": trough["date"],
        "avg_cash_pct": sum(cash_pct) / len(cash_pct) if cash_pct else None,
    }
    pairs = [
        ("Sessions", raw["sessions"]),
        ("Period", f"{raw['first_date']} -> {raw['last_date']}"),
        ("Start value", _num(raw["start_value"])),
        ("End value", _num(raw["end_value"])),
        ("Peak", f"{_num(raw['peak_value'])}  ({raw['peak_date']})"),
        ("Trough", f"{_num(raw['trough_value'])}  ({raw['trough_date']})"),
    ]
    if raw["avg_cash_pct"] is not None:
        pairs.append(("Avg cash %", _num(raw["avg_cash_pct"])))
    _kv("equity_summary", "Equity curve", pairs, raw, opts)
