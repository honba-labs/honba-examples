"""candles/04_multi_timeframe: one 1m fetch, several views, and the alignment checks."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

from honba_examples.bars import aggregate, bucket_start
from tests.synthetic import bar, weekdays

_PATH = Path(__file__).resolve().parents[2] / "candles" / "04_multi_timeframe.py"
_NS_PER_SECOND = 1_000_000_000
_PAIRS = (("1m", "5m"), ("5m", "1h"), ("1h", "1d"))


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("multi_timeframe", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def source(example):
    """The single fetch every view is derived from, re-read straight from the adapter."""
    return asyncio.run(example._fetch_bars("fake", {}))


@pytest.fixture(scope="module")
def report(example):
    return example.run()


def _ns(iso: str) -> int:
    """Exact unix nanoseconds of an ISO-8601 stamp, without a float round trip."""
    moment = dt.datetime.fromisoformat(iso)
    delta = moment - dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    return (delta.days * 86_400 + delta.seconds) * _NS_PER_SECOND + delta.microseconds * 1_000


def _groups(rows, timeframe):
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(bucket_start(_ns(row["date"]), timeframe), []).append(row)
    return grouped


def _fold(members):
    """The OHLCV rule, written here independently of ``honba_examples.bars``."""
    return (
        members[0]["open"],
        max(row["high"] for row in members),
        min(row["low"] for row in members),
        members[-1]["close"],
        sum(row["volume"] for row in members),
    )


def _daily_bars(days):
    return [bar("RELIANCE", day, 100.0 + i, close=101.0 + i) for i, day in enumerate(days)]


def test_report_lists_every_view_and_an_alignment_block(report):
    assert set(report) == {
        "adapter",
        "instrument",
        "source_timeframe",
        "timeframes",
        "1m",
        "5m",
        "1h",
        "1d",
        "alignment",
    }
    assert report["adapter"] == "fake"
    assert report["instrument"] == {"symbol": "RELIANCE", "exchange": "NSE"}
    assert report["source_timeframe"] == "1m"
    assert report["timeframes"] == ["1m", "5m", "1h", "1d"]
    assert set(report["alignment"]) == {"1m->5m", "5m->1h", "1h->1d"}
    for timeframe in report["timeframes"]:
        section = report[timeframe]
        assert set(section) == {"bar_count", "volume", "first_date", "last_date", "bars"}
        assert section["bar_count"] == len(section["bars"]) > 0
        assert section["first_date"] <= section["last_date"]
        assert section["volume"] == sum(row["volume"] for row in section["bars"])
        dates = [row["date"] for row in section["bars"]]
        assert dates == sorted(dates)
        assert len(set(dates)) == len(dates)
        for row in section["bars"]:
            assert set(row) == {"date", "open", "high", "low", "close", "volume"}


def test_the_source_view_is_exactly_the_fetch(report, source):
    assert report["1m"]["bar_count"] == len(source)
    for row, bar_ in zip(report["1m"]["bars"], source, strict=True):
        assert _ns(row["date"]) == bar_.ts
        assert (row["open"], row["high"], row["low"], row["close"], row["volume"]) == (
            bar_.open,
            bar_.high,
            bar_.low,
            bar_.close,
            bar_.volume,
        )


@pytest.mark.parametrize("fine_timeframe, coarse_timeframe", _PAIRS)
def test_every_fine_bar_lands_in_exactly_one_coarse_bar(report, fine_timeframe, coarse_timeframe):
    fine = report[fine_timeframe]["bars"]
    coarse = report[coarse_timeframe]["bars"]
    groups = _groups(fine, coarse_timeframe)
    fine_buckets = [bucket_start(_ns(row["date"]), coarse_timeframe) for row in fine]
    coarse_buckets = [bucket_start(_ns(row["date"]), coarse_timeframe) for row in coarse]
    assert len(fine_buckets) == len(fine) == report[fine_timeframe]["bar_count"]
    assert len(coarse_buckets) == len(set(coarse_buckets))
    assert set(fine_buckets) == set(groups) == set(coarse_buckets)
    assert len(coarse_buckets) == len(groups)
    assert all(coarse_buckets.count(bucket) == 1 for bucket in fine_buckets)
    assert sum(len(members) for members in groups.values()) == len(fine)


@pytest.mark.parametrize("fine_timeframe, coarse_timeframe", _PAIRS)
def test_each_coarse_bar_equals_the_fold_of_its_own_constituents(
    report, fine_timeframe, coarse_timeframe
):
    groups = _groups(report[fine_timeframe]["bars"], coarse_timeframe)
    for row in report[coarse_timeframe]["bars"]:
        members = groups[bucket_start(_ns(row["date"]), coarse_timeframe)]
        expected = _fold(members)
        assert (row["open"], row["high"], row["low"], row["close"]) == expected[:4]
        assert row["volume"] == expected[4]
        assert row["date"] == members[0]["date"]


@pytest.mark.parametrize("fine_timeframe, coarse_timeframe", _PAIRS)
def test_counts_and_volumes_reconcile_across_the_pair(report, fine_timeframe, coarse_timeframe):
    check = report["alignment"][f"{fine_timeframe}->{coarse_timeframe}"]
    assert check["fine_timeframe"] == fine_timeframe
    assert check["coarse_timeframe"] == coarse_timeframe
    assert check["fine_count"] == report[fine_timeframe]["bar_count"]
    assert check["coarse_count"] == report[coarse_timeframe]["bar_count"]
    assert check["constituents_total"] == check["fine_count"]
    assert check["volume_fine"] == check["volume_coarse"]
    assert check["volume_fine"] == report[fine_timeframe]["volume"]
    assert check["covered"] is True
    assert check["fold_match"] is True
    assert check["span_match"] is True
    assert check["aligned"] is True


def test_every_level_covers_the_same_span_and_volume(report):
    volumes = [report[timeframe]["volume"] for timeframe in report["timeframes"]]
    assert volumes == [volumes[0]] * len(volumes)
    for timeframe in ("5m", "1h", "1d"):
        assert _ns(report["1m"]["first_date"]) == _ns(report[timeframe]["first_date"])
        assert bucket_start(_ns(report["1m"]["last_date"]), timeframe) == bucket_start(
            _ns(report[timeframe]["last_date"]), timeframe
        )


def test_output_is_deterministic_across_two_runs(example):
    first = json.dumps(example.run(), sort_keys=True)
    second = json.dumps(example.run(), sort_keys=True)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "multi_timeframe.json"
    assert example.main(["--out", str(out)]) == 0
    stdout_json = json.loads(capsys.readouterr().out)
    assert json.loads(out.read_text()) == stdout_json
    assert stdout_json["timeframes"] == ["1m", "5m", "1h", "1d"]


def test_the_check_passes_on_multi_day_bars(example):
    """The fake's history never leaves one UTC day, so 1d is proved on synthetic bars."""
    days = weekdays(dt.date(2026, 6, 1), 7)
    fine = _daily_bars(days)
    check = example._alignment(fine, "1d", aggregate(fine, "1w"), "1w")
    assert check["fine_count"] == 7
    assert check["constituents_total"] == 7
    assert check["covered"] is True
    assert check["fold_match"] is True
    assert check["span_match"] is True
    assert check["aligned"] is True


def test_the_check_notices_a_missing_coarse_bar(example):
    days = weekdays(dt.date(2026, 6, 1), 10)  # two Monday-start ISO weeks
    fine = _daily_bars(days)
    check = example._alignment(fine, "1d", aggregate(fine, "1w")[:1], "1w")
    assert (check["fine_count"], check["coarse_count"]) == (10, 1)
    assert check["constituents_total"] == 5
    assert check["covered"] is False
    assert check["span_match"] is False
    assert check["aligned"] is False


def test_the_check_notices_a_wrong_close(example):
    days = weekdays(dt.date(2026, 6, 1), 10)
    fine = _daily_bars(days)
    coarse = aggregate(fine, "1w")
    tampered = [dataclasses.replace(coarse[0], close=coarse[0].close + 5.0), *coarse[1:]]
    check = example._alignment(fine, "1d", tampered, "1w")
    assert check["covered"] is True
    assert check["fold_match"] is False
    assert check["span_match"] is True
    assert check["aligned"] is False
