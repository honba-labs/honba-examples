"""honba_examples.sip_plan / sip_report: schedule, XIRR and report helpers for script 08."""

from __future__ import annotations

import argparse
import datetime as dt
import json

import pytest

from honba_examples.sip_plan import (
    calculate_xirr,
    monthly_dates,
    sip_target_dates,
    weekly_dates,
)
from honba_examples.sip_report import add_sip_args, emit_sip_report


def test_monthly_dates_clamps_day_to_month_end():
    out = monthly_dates(dt.date(2025, 1, 1), dt.date(2025, 4, 30), sip_day=31)
    assert out == [
        dt.date(2025, 1, 31),
        dt.date(2025, 2, 28),
        dt.date(2025, 3, 31),
        dt.date(2025, 4, 30),
    ]


def test_monthly_dates_skips_targets_before_start():
    out = monthly_dates(dt.date(2025, 1, 15), dt.date(2025, 3, 31), sip_day=1)
    assert out == [dt.date(2025, 2, 1), dt.date(2025, 3, 1)]


def test_weekly_dates_starts_on_first_matching_weekday():
    out = weekly_dates(dt.date(2025, 1, 1), dt.date(2025, 1, 20), sip_weekday=0)  # Wed start
    assert out == [dt.date(2025, 1, 6), dt.date(2025, 1, 13), dt.date(2025, 1, 20)]


def test_sip_target_dates_dispatch_and_error():
    a, b = dt.date(2025, 1, 1), dt.date(2025, 3, 1)
    assert sip_target_dates(" Monthly ", a, b) == monthly_dates(a, b)
    assert sip_target_dates("weekly", a, b, sip_weekday=2) == weekly_dates(a, b, 2)
    with pytest.raises(ValueError, match="Unsupported frequency"):
        sip_target_dates("daily", a, b)


def test_calculate_xirr_edge_cases():
    d = dt.date(2025, 1, 1)
    assert calculate_xirr([(d, -1.0)]) is None
    assert calculate_xirr([(d, -1.0), (dt.date(2026, 1, 1), -2.0)]) is None
    rate = calculate_xirr([(d, -1000.0), (dt.date(2026, 1, 1), 1100.0)])
    assert rate is not None
    assert rate == pytest.approx(10.0, abs=0.1)


def _result(xirr):
    s = {
        "symbol": "T", "exchange": "NSE", "frequency": "monthly", "first_session": "a",
        "last_session": "b", "sessions_count": 1, "sip_amount": 1.0, "initial_corpus": 0.0,
        "installments_count": 1, "total_invested": 1.0, "total_units": 1.0,
        "avg_purchase_price": 1.0, "final_market_price": 1.0, "final_portfolio_value": 1.0,
        "net_pnl": 0.0, "total_return_pct": 0.0, "cagr_pct": 0.0, "xirr_pct": xirr,
        "max_drawdown_pct": 0.0, "total_fees_paid": 0.0,
    }  # fmt: skip
    return {"summary": s}


def test_add_sip_args_defaults_and_aliases():
    p = argparse.ArgumentParser()
    add_sip_args(p)
    a = p.parse_args(
        ["--instrument", "X", "--capital", "5", "--no-of-sip", "3", "--start", "2025-01-02"]
    )
    assert (a.symbol, a.initial_corpus, a.sip_count, a.start) == ("X", 5.0, 3, dt.date(2025, 1, 2))
    assert (a.format, a.out, a.download) == ("table", None, False)


def test_emit_sip_report_table_na_and_json_out(tmp_path, capsys):
    p = argparse.ArgumentParser()
    add_sip_args(p)
    emit_sip_report(_result(None), p.parse_args([]))
    assert "XIRR (Annualized)   : N/A" in capsys.readouterr().out

    out = tmp_path / "sub" / "r.json"
    emit_sip_report(_result(12.3456), p.parse_args(["--format", "json", "--out", str(out)]))
    printed = capsys.readouterr().out
    assert f"[Output] Wrote full report to {out}" in printed
    assert json.loads(out.read_text())["summary"]["xirr_pct"] == 12.3456
