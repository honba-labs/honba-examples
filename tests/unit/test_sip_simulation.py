"""honba_examples.sip.simulate_portfolio_sip on a tiny synthetic NSE catalog."""

from __future__ import annotations

import datetime as dt

import pytest

from honba_examples.sip import simulate_portfolio_sip, sip_data_notes

ASOF = dt.date(2024, 6, 26)  # a Wednesday: the screen's last close
NEXT_SESSION = dt.date(2024, 6, 27)
END = dt.date(2024, 9, 30)


def _weekdays_ending(end: dt.date, n: int) -> list[dt.date]:
    days: list[dt.date] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= dt.timedelta(days=1)
    return days[::-1]


@pytest.fixture
def catalog(catalog_dir, add_series):
    days = _weekdays_ending(END, 90)
    add_series("ALIVE", [100.0 + i for i in range(90)], last_date=END)
    add_series("GONE", [50.0] * 40, last_date=ASOF)  # last bar is the asof session itself
    add_series("HALTED", [200.0] * 70, last_date=dt.date(2024, 7, 31))  # stops mid-window
    return catalog_dir, {d: 100.0 + i for i, d in enumerate(days)}


def _run(catalog_dir, symbols, **kw):
    return simulate_portfolio_sip(
        catalog_dir=catalog_dir,
        symbols=symbols,
        start_date=ASOF,
        end_date=END,
        initial_capital=100_000.0,
        monthly_sip=None,
        **kw,
    )


def test_lump_sum_buys_the_next_session_open_not_the_asof_open(catalog):
    catalog_dir, price = catalog
    res = _run(catalog_dir, ["ALIVE"])
    row = res["stocks"][0]
    assert row["first_date"] == NEXT_SESSION.isoformat()
    assert row["initial_price"] == pytest.approx(price[NEXT_SESSION])
    assert row["initial_price"] != pytest.approx(price[ASOF])
    assert row["shares_held"] * row["initial_price"] == pytest.approx(100_000.0)


def test_a_saturday_asof_still_buys_the_following_monday(catalog):
    catalog_dir, price = catalog
    res = simulate_portfolio_sip(
        catalog_dir=catalog_dir,
        symbols=["ALIVE"],
        start_date=dt.date(2024, 6, 29),
        end_date=END,
        initial_capital=100_000.0,
    )
    assert res["stocks"][0]["first_date"] == "2024-07-01"
    assert res["stocks"][0]["initial_price"] == pytest.approx(price[dt.date(2024, 7, 1)])


def test_symbols_without_bars_after_asof_are_reported_unpriced_and_delisted(catalog):
    catalog_dir, _ = catalog
    res = _run(catalog_dir, ["ALIVE", "GONE"])
    summary = res["summary"]
    assert summary["unpriced_symbols"] == ["GONE"]
    assert "GONE" in summary["delisted_symbols"]
    gone = next(r for r in res["stocks"] if r["symbol"] == "GONE")
    assert gone["status"] == "unpriced" and gone["delisted"] is True
    assert "No bars after" in gone["confirmation"]
    alive = next(r for r in res["stocks"] if r["symbol"] == "ALIVE")
    assert alive["status"] == "ok" and alive["delisted"] is False


def test_symbols_that_stop_mid_window_are_flagged_delisted(catalog):
    catalog_dir, _ = catalog
    res = _run(catalog_dir, ["ALIVE", "HALTED"])
    halted = next(r for r in res["stocks"] if r["symbol"] == "HALTED")
    assert halted["status"] == "stale" and halted["delisted"] is True
    assert halted["last_date"] == "2024-07-31"
    assert "Delisted" in halted["confirmation"]
    assert res["summary"]["delisted_symbols"] == ["HALTED"]
    assert res["summary"]["unpriced_symbols"] == []


def test_symbols_with_no_data_are_reported_skipped(catalog):
    catalog_dir, _ = catalog
    res = _run(catalog_dir, ["ALIVE", "NODATA"])
    assert res["summary"]["skipped_symbols"] == ["NODATA"]
    assert [r["symbol"] for r in res["stocks"]] == ["ALIVE"]


def test_all_symbols_without_data_do_not_invent_gains(catalog):
    catalog_dir, _ = catalog
    res = _run(catalog_dir, ["NODATA"])
    assert res["summary"]["skipped_symbols"] == ["NODATA"]
    assert res["stocks"][0]["status"] == "no_data"
    assert "Modest Gain" not in res["stocks"][0]["confirmation"]


def test_clean_run_has_empty_reports(catalog):
    catalog_dir, _ = catalog
    summary = _run(catalog_dir, ["ALIVE"])["summary"]
    assert summary["unpriced_symbols"] == []
    assert summary["skipped_symbols"] == []
    assert summary["delisted_symbols"] == []
    assert sip_data_notes(summary) == []


def test_data_notes_name_every_problem_symbol(catalog):
    catalog_dir, _ = catalog
    summary = _run(catalog_dir, ["ALIVE", "GONE", "HALTED", "NODATA"])["summary"]
    text = "\n".join(sip_data_notes(summary))
    for name in ("GONE", "HALTED", "NODATA"):
        assert name in text
    assert "ALIVE" not in text


def test_backtest_table_and_pipeline_summary_print_the_notes(catalog):
    import io

    from rich.console import Console

    from honba_examples.pipeline.base import PipelineResult
    from honba_examples.screening.render import render_backtest_table

    catalog_dir, _ = catalog
    res = _run(catalog_dir, ["ALIVE", "GONE", "NODATA"])
    buffer = io.StringIO()
    console = Console(file=buffer, width=200, force_terminal=False)
    render_backtest_table(res, ASOF, END, "equal", console)
    text = buffer.getvalue()
    assert "GONE" in text and "NODATA" in text and "No bars after" in text

    out = io.StringIO()
    import contextlib

    with contextlib.redirect_stdout(out):
        PipelineResult({"summary": res["summary"], "stocks": res["stocks"]}).print_summary()
    assert "GONE" in out.getvalue()
