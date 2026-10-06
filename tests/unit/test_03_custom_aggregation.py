"""candles/03_custom_aggregation: one 1m fetch folded into a timeframe chosen on the CLI."""

from __future__ import annotations

import asyncio
import datetime as dt
import importlib.util
import json
import math
from pathlib import Path

import pytest
from honba.adapters.testing import FakeAdapter

from honba_examples.bars import SUPPORTED_TIMEFRAMES, bucket_start, timeframe_seconds

_PATH = Path(__file__).resolve().parents[2] / "candles" / "03_custom_aggregation.py"
_NS_PER_SECOND = 1_000_000_000


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("custom_aggregation", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def source(example):
    """The very bars ``run`` fetches, so every expectation is derived, never hard-coded."""
    return asyncio.run(example._fetch_bars("fake", {}))


@pytest.fixture(scope="module")
def report(example):
    return example.run(timeframe="5m")


@pytest.fixture
def requested_timeframes(monkeypatch):
    """Every timeframe the example hands to ``historical_bars``, in order."""
    seen: list[str] = []
    original = FakeAdapter.historical_bars

    async def spy(self, instrument_id, *, timeframe, start, end):
        seen.append(timeframe)
        return await original(self, instrument_id, timeframe=timeframe, start=start, end=end)

    monkeypatch.setattr(FakeAdapter, "historical_bars", spy)
    return seen


def _ns(iso: str) -> int:
    """Exact unix nanoseconds of an ISO-8601 stamp, without a float round trip."""
    moment = dt.datetime.fromisoformat(iso)
    delta = moment - dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    return (delta.days * 86_400 + delta.seconds) * _NS_PER_SECOND + delta.microseconds * 1_000


def _groups(bars, timeframe):
    grouped: dict[int, list] = {}
    for bar in bars:
        grouped.setdefault(bucket_start(bar.ts, timeframe), []).append(bar)
    return {bucket: sorted(members, key=lambda b: b.ts) for bucket, members in grouped.items()}


def test_report_states_the_rule_the_counts_and_the_row_shape(report):
    assert set(report) == {
        "adapter",
        "instrument",
        "source_timeframe",
        "target_timeframe",
        "rule",
        "input_count",
        "output_count",
        "bars",
    }
    assert report["adapter"] == "fake"
    assert report["instrument"] == {"symbol": "RELIANCE", "exchange": "NSE"}
    assert report["source_timeframe"] == "1m"
    assert report["target_timeframe"] == "5m"
    assert "first" in report["rule"] and "last" in report["rule"]
    assert "right-open" in report["rule"]
    assert report["input_count"] > report["output_count"] > 0
    for row in report["bars"]:
        assert set(row) == {"date", "open", "high", "low", "close", "volume", "constituents"}
        assert row["high"] >= max(row["open"], row["close"])
        assert row["low"] <= min(row["open"], row["close"])


def test_output_count_is_the_number_of_buckets_the_source_falls_into(report, source):
    grouped = _groups(source, "5m")
    per_bucket = timeframe_seconds("5m") // timeframe_seconds("1m")
    assert report["input_count"] == len(source)
    assert report["output_count"] == len(grouped)
    assert math.ceil(len(source) / per_bucket) <= report["output_count"] <= len(source)
    assert all(1 <= len(members) <= per_bucket for members in grouped.values())


def test_each_row_is_the_fold_of_its_own_constituent_bars(report, source):
    grouped = _groups(source, "5m")
    bucket_ns = timeframe_seconds("5m") * _NS_PER_SECOND
    for row, (bucket, members) in zip(report["bars"], sorted(grouped.items()), strict=True):
        assert row["constituents"] == len(members)
        assert (
            row["date"]
            == dt.datetime.fromtimestamp(members[0].ts / 1e9, tz=dt.timezone.utc).isoformat()
        )
        assert row["open"] == members[0].open
        assert row["close"] == members[-1].close
        assert row["high"] == max(bar.high for bar in members)
        assert row["low"] == min(bar.low for bar in members)
        assert row["volume"] == sum(bar.volume for bar in members)
        for bar in members:  # right-open bucket: [start, start + timeframe)
            assert bucket <= bar.ts < bucket + bucket_ns


def test_constituents_sum_to_the_input_count(report):
    assert sum(row["constituents"] for row in report["bars"]) == report["input_count"]


def test_output_is_deterministic_across_two_runs(example):
    first = json.dumps(example.run(timeframe="5m"), sort_keys=True)
    second = json.dumps(example.run(timeframe="5m"), sort_keys=True)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "aggregation.json"
    assert example.main(["--timeframe", "5m", "--out", str(out)]) == 0
    stdout_json = json.loads(capsys.readouterr().out)
    assert json.loads(out.read_text()) == stdout_json
    assert stdout_json["target_timeframe"] == "5m"


def test_main_names_the_supported_vocabulary_for_an_unknown_timeframe(example, capsys):
    with pytest.raises(SystemExit) as excinfo:
        example.main(["--timeframe", "2h"])
    message = str(excinfo.value)
    assert "2h" in message
    assert all(timeframe in message for timeframe in SUPPORTED_TIMEFRAMES)
    assert "Traceback" not in capsys.readouterr().err


def test_an_unknown_timeframe_fails_before_the_adapter_is_asked(example, requested_timeframes):
    with pytest.raises(ValueError, match="2h"):
        example.run(timeframe="2h")
    assert requested_timeframes == []


@pytest.mark.parametrize("target", ["1h", "1w"])
def test_a_timeframe_the_adapter_cannot_serve_is_derived_locally(
    example, requested_timeframes, target
):
    result = example.run(timeframe=target)
    assert result["target_timeframe"] == target
    assert result["input_count"] > result["output_count"] > 0
    assert requested_timeframes == ["1m"]
