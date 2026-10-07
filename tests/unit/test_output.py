"""honba_examples.output: example-oriented presets over honba.display.

Fixed width, no colour, synthetic data. Snapshot: tests/snapshots/output_report.txt
(rewrite with HONBA_BLESS=1 after an intended layout change).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
from pathlib import Path

import pytest

from honba_examples import output as ex_out

SNAP = Path(__file__).resolve().parents[1] / "snapshots"

TRADES = [
    {"symbol": "AAA", "side": "buy", "qty": 10, "price": 100.0, "notional": 1000.0, "fee": 1.5},
    {"symbol": "BBB", "side": "sell", "qty": 4, "price": 50.0, "notional": 200.0, "fee": 0.5},
    {"symbol": "AAA", "side": "buy", "qty": 5, "price": 110.0, "notional": 550.0, "fee": 0.75},
]
REBALANCES = [{"date": "2026-06-01", "trades": TRADES}, {"date": "2026-06-08", "trades": []}]
METRICS = {
    "final_value": 1_012_500.5,
    "total_return_pct": 1.25,
    "cagr_pct": 9.5,
    "max_drawdown_pct": -2.5,
    "sharpe": 1.2345,
    "total_fees": 123.45,
    "turnover": 0.5,
    "traded_notional": 1750.0,
    "n_fills": 3,
    "n_rebalances": 2,
}
CURVE = [
    {"date": "2026-06-01", "value": 1_000_000.0, "cash": 100_000.0},
    {"date": "2026-06-02", "value": 1_020_000.0, "cash": 80_000.0},
    {"date": "2026-06-03", "value": 990_000.0, "cash": 90_000.0},
]


def _opts(fmt: str = "table", width: int = 78) -> ex_out.OutputOptions:
    return ex_out.OutputOptions(fmt=ex_out.OutputFormat.parse(fmt), width=width, out=io.StringIO())


def _text(o: ex_out.OutputOptions) -> str:
    return o.out.getvalue()


@pytest.fixture(autouse=True)
def _no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")


def test_add_output_args_and_resolve_defaults_and_flags() -> None:
    p = argparse.ArgumentParser()
    ex_out.add_output_args(p)
    d = ex_out.resolve_output(p.parse_args([]))
    assert d.fmt is ex_out.OutputFormat.TABLE and d.width is None
    o = ex_out.resolve_output(p.parse_args(["--format", "JSON", "--width", "90"]))
    assert o.fmt is ex_out.OutputFormat.JSON and o.width == 90


def test_bad_format_is_an_argparse_error() -> None:
    p = argparse.ArgumentParser()
    ex_out.add_output_args(p)
    with pytest.raises(SystemExit):
        p.parse_args(["--format", "xml"])


def test_run_header_skips_missing_values_and_formats_capital() -> None:
    o = _opts("plain")
    ex_out.print_run_header(
        {
            "strategy": "ewr",
            "universe": "nifty50",
            "start": "2026-01-01",
            "end": "2026-06-30",
            "capital": 1_000_000.0,
            "settlement_days": 1,
            "backend": None,
        },
        o,
    )
    t = _text(o)
    for needle in (
        "Strategy",
        "ewr",
        "Universe",
        "Period",
        "2026-01-01 -> 2026-06-30",
        "Capital",
        "T+1",
    ):
        assert needle in t
    assert "Backend" not in t


def test_metrics_block_formats_by_kind_and_orders_known_first() -> None:
    o = _opts("plain")
    ex_out.print_metrics(METRICS, o)
    t = _text(o)
    assert "+1.25%" in t and "-2.50%" in t and "1.234" in t
    assert "1,012,500.50" in t
    assert t.index("Final value") < t.index("Sharpe") < t.index("Fills")


def test_changes_aggregate_net_shares_per_symbol_and_date() -> None:
    o = _opts("plain")
    ex_out.print_changes(REBALANCES, o)
    t = _text(o)
    for h in ("Date", "Symbol", "Side", "Net shares", "Price", "Notional", "Cost"):
        assert h in t
    aaa = next(ln for ln in t.splitlines() if ln.startswith("2026-06-01") and "AAA" in ln)
    assert "+15" in aaa and "1,550.00" in aaa and "2.25" in aaa
    bbb = next(ln for ln in t.splitlines() if "BBB" in ln)
    assert "-4" in bbb and "SELL" in bbb


def test_changes_with_no_trades_still_prints_headers() -> None:
    o = _opts("plain")
    ex_out.print_changes([{"date": "2026-06-08", "trades": []}], o)
    assert "Net shares" in _text(o)


def test_holdings_with_prices_show_value() -> None:
    o = _opts("plain")
    ex_out.print_holdings({"BBB": 4, "AAA": 15}, o, prices={"AAA": 110.0, "BBB": 50.0})
    lines = _text(o).splitlines()
    assert lines[3].startswith("AAA")  # sorted by symbol
    assert "1,650.00" in _text(o)


def test_rejections_table_fills_missing_keys_with_dash() -> None:
    o = _opts("plain")
    ex_out.print_rejections([{"date": "2026-06-02", "symbol": "AAA", "reason": "no cash"}], o)
    t = _text(o)
    assert "Reason" in t and "no cash" in t and "-" in t


def test_notes_lists_missing_and_never_held() -> None:
    o = _opts("plain")
    ex_out.print_data_notes(o, missing_data=["IDEA"], never_held=["MRF", "PAGEIND"])
    t = _text(o)
    assert "No Parquet bars for 1 basket members: IDEA" in t
    assert "Never held" in t and "MRF, PAGEIND" in t


def test_notes_print_nothing_when_empty() -> None:
    o = _opts("plain")
    ex_out.print_data_notes(o, missing_data=[], never_held=[])
    assert _text(o) == ""


def test_equity_summary_reports_peak_trough_and_final() -> None:
    o = _opts("plain")
    ex_out.print_equity_summary(CURVE, o)
    t = _text(o)
    assert "Sessions" in t and "3" in t
    assert "1,020,000.00" in t and "2026-06-02" in t  # peak
    assert "990,000.00" in t and "2026-06-03" in t  # trough / final


def test_json_blocks_are_named_and_keyed() -> None:
    o = _opts("json")
    ex_out.print_metrics(METRICS, o)
    doc = json.loads(_text(o))
    assert doc["block"] == "metrics"
    assert doc["rows"][0] == {"metric": "final_value", "value": 1_012_500.5}


def test_csv_changes_are_machine_readable() -> None:
    o = _opts("csv")
    ex_out.print_changes(REBALANCES, o)
    rows = list(csv.DictReader(io.StringIO(_text(o))))
    assert list(rows[0]) == ["date", "symbol", "side", "net_shares", "price", "notional", "cost"]
    aaa = next(r for r in rows if r["symbol"] == "AAA")
    assert aaa["net_shares"] == "15" and float(aaa["notional"]) == 1550.0


def test_full_report_matches_snapshot() -> None:
    o = _opts("table", 78)
    ex_out.print_run_header(
        {
            "strategy": "ewr",
            "universe": "nifty200_alpha30",
            "start": "2026-06-01",
            "end": "2026-06-03",
            "capital": 1_000_000.0,
            "settlement_days": 1,
            "backend": "python",
        },
        o,
    )
    ex_out.print_metrics(METRICS, o)
    ex_out.print_changes(REBALANCES, o)
    ex_out.print_holdings({"AAA": 15, "BBB": 4}, o, prices={"AAA": 110.0, "BBB": 50.0})
    ex_out.print_rejections([{"date": "2026-06-02", "symbol": "AAA", "reason": "no cash"}], o)
    ex_out.print_data_notes(o, missing_data=["IDEA"], never_held=["MRF"])
    ex_out.print_equity_summary(CURVE, o)
    text = _text(o)
    assert "\x1b[" not in text and all(len(ln) <= 78 for ln in text.splitlines())
    path = SNAP / "output_report.txt"
    if os.environ.get("HONBA_BLESS") == "1":
        path.write_text(text, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == text


def test_base_example_exposes_the_shared_output_flags() -> None:
    from honba_examples.base import HonbaExample

    ex = HonbaExample()
    ex.parse_args(["--format", "csv", "--width", "100"])
    assert ex.output.fmt is ex_out.OutputFormat.CSV and ex.output.width == 100
