"""candles/01_historical_candles: pull a half-open window of bars from the fake adapter."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest
from honba.adapters.errors import AdapterError

_PATH = Path(__file__).resolve().parents[2] / "candles" / "01_historical_candles.py"
#: First bar of the fake's documented session; every later bar is one minute after it.
_SESSION_START = dt.datetime(2025, 6, 2, 9, 15, tzinfo=dt.timezone.utc)


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("historical_candles", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def report(example):
    return example.run()


def test_report_shape_keys_and_row_fields(report):
    assert set(report) == {
        "adapter",
        "instrument",
        "timeframe",
        "window",
        "count",
        "bars",
        "summary",
    }
    assert report["adapter"] == "fake"
    assert report["instrument"] == {"symbol": "RELIANCE", "exchange": "NSE"}
    assert report["timeframe"] == "1m"
    assert set(report["window"]) == {"start", "end"}
    assert report["window"]["start"] == _SESSION_START.isoformat()
    end = dt.datetime.fromisoformat(report["window"]["end"])
    assert end - _SESSION_START == dt.timedelta(minutes=5)
    assert end.tzinfo is not None
    assert set(report["summary"]) == {"first_date", "last_date", "min_low", "max_high"}
    for row in report["bars"]:
        assert set(row) == {"date", "open", "high", "low", "close", "volume"}


def test_default_window_holds_the_fakes_five_bars(report):
    # bars_per_request=5 is the fake's default and [09:15, 09:20) contains all five of them.
    assert report["count"] == len(report["bars"]) == 5
    assert report["count"] > 0


def test_bars_ascend_and_keep_the_ohlc_invariants(report):
    dates = [row["date"] for row in report["bars"]]
    assert dates == sorted(dates)
    assert len(set(dates)) == len(dates)
    for row in report["bars"]:
        assert row["low"] <= min(row["open"], row["close"])
        assert max(row["open"], row["close"]) <= row["high"]
        assert row["volume"] > 0


def test_dates_are_the_documented_fake_session_dates(report):
    expected = [
        (_SESSION_START + dt.timedelta(minutes=offset)).isoformat()
        for offset in range(report["count"])
    ]
    assert [row["date"] for row in report["bars"]] == expected


def test_summary_describes_the_rows_it_summarises(report):
    rows = report["bars"]
    summary = report["summary"]
    assert summary["first_date"] == rows[0]["date"]
    assert summary["last_date"] == rows[-1]["date"]
    assert summary["min_low"] == min(row["low"] for row in rows)
    assert summary["max_high"] == max(row["high"] for row in rows)


def test_window_is_half_open_including_the_start_bound(example):
    first = example.run(start=_SESSION_START, end=_SESSION_START + dt.timedelta(minutes=1))
    assert [row["date"] for row in first["bars"]] == [_SESSION_START.isoformat()]
    second = example.run(
        start=_SESSION_START + dt.timedelta(minutes=1),
        end=_SESSION_START + dt.timedelta(minutes=2),
    )
    assert [row["date"] for row in second["bars"]] == [
        (_SESSION_START + dt.timedelta(minutes=1)).isoformat()
    ]


def test_a_naive_window_bound_is_rejected_with_a_clear_message(example):
    with pytest.raises(ValueError, match="tz-aware"):
        # exactly what an offset-less --start would parse to
        example.run(start=dt.datetime.fromisoformat("2025-06-02T09:15:00"))


def test_an_unsupported_timeframe_raises_adapter_error(example):
    with pytest.raises(AdapterError, match="1w"):
        example.run(timeframe="1w")


def test_an_unknown_instrument_raises_adapter_error(example):
    with pytest.raises(AdapterError, match="unknown instrument"):
        example.run(instrument="INFY")


def test_output_is_deterministic_across_two_calls(example):
    first = json.dumps(example.run(), sort_keys=True)
    second = json.dumps(example.run(), sort_keys=True)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "bars.json"
    assert example.main(["--out", str(out)]) == 0
    stdout_json = json.loads(capsys.readouterr().out)
    assert json.loads(out.read_text()) == stdout_json
    assert stdout_json["count"] > 0


def test_main_reports_an_unsupported_timeframe_without_a_traceback(example, capsys):
    with pytest.raises(SystemExit) as excinfo:
        example.main(["--timeframe", "1w"])
    captured = capsys.readouterr()
    assert "1w" in str(excinfo.value)
    assert "Traceback" not in captured.err


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main([]) == 0
    assert json.loads(capsys.readouterr().out)["count"] > 0
