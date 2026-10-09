"""candles/02_realtime_candles: fold pushed quote ticks into OHLC frames, offline."""

from __future__ import annotations

import asyncio
import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest
from honba.adapters.errors import AdapterError
from honba.domain.tick import QuoteTick

_PATH = Path(__file__).resolve().parents[2] / "candles" / "02_realtime_candles.py"
_NS_PER_MINUTE = 60 * 1_000_000_000


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("realtime_candles", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def report(example):
    return example.run()


def test_report_shape_keys_and_row_fields(report):
    assert set(report) == {"adapter", "advances", "timeframe", "subscription", "ticks", "frames"}
    assert report["adapter"] == "fake"
    assert report["advances"] == 3
    assert report["timeframe"] == "1m"
    subscription = report["subscription"]
    assert subscription["id"].startswith("sub-")
    assert subscription["mode"] == "quote"
    assert [instrument["symbol"] for instrument in subscription["instruments"]] == ["RELIANCE"]
    for tick in report["ticks"]:
        assert {"symbol", "ts", "bid_price", "ask_price", "mid_price"} <= set(tick)
    for frame in report["frames"]:
        assert set(frame) == {"date", "open", "high", "low", "close", "tick_count"}


def test_one_pushed_tick_per_advance_round(report):
    # advance_prices moves the series and pushes exactly one QuoteTick per call, so the
    # single subscription sees one tick per round, inline before the call returns.
    ticks = report["ticks"]
    assert len(ticks) == report["advances"]
    stamps = [tick["ts"] for tick in ticks]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps)
    for tick in ticks:
        assert tick["bid_price"] <= tick["ask_price"]
        assert tick["mid_price"] == (tick["bid_price"] + tick["ask_price"]) / 2


def test_frames_are_the_minute_fold_of_the_reported_ticks(report):
    # The expectation is recomputed here from the reported ticks: group them by their
    # UTC minute and fold each group, rather than assuming one frame per advance round.
    grouped: dict[int, list[float]] = {}
    for tick in report["ticks"]:
        grouped.setdefault(tick["ts"] // _NS_PER_MINUTE, []).append(tick["mid_price"])
    expected = [
        {
            "date": dt.datetime.fromtimestamp(
                minute * _NS_PER_MINUTE / 1e9, tz=dt.timezone.utc
            ).isoformat(),
            "open": mids[0],
            "high": max(mids),
            "low": min(mids),
            "close": mids[-1],
            "tick_count": len(mids),
        }
        for minute, mids in grouped.items()
    ]
    assert report["frames"] == expected
    # The fake's clock ticks 1 ms per event, so a run's ticks share one minute bucket.
    assert len(report["frames"]) == 1
    assert sum(frame["tick_count"] for frame in report["frames"]) == len(report["ticks"])


def test_frame_ohlc_follows_the_pushed_tick_stream(report):
    mids = [tick["mid_price"] for tick in report["ticks"]]
    frame = report["frames"][0]
    assert frame["open"] == mids[0]
    assert frame["close"] == mids[-1]
    assert frame["high"] == max(mids)
    assert frame["low"] == min(mids)
    assert frame["low"] <= min(frame["open"], frame["close"])
    assert max(frame["open"], frame["close"]) <= frame["high"]
    assert frame["tick_count"] == len(mids) == report["advances"]


def test_fold_opens_a_new_frame_when_the_minute_changes(example):
    instrument_id = example._INSTRUMENT

    def tick(at: dt.datetime, bid: float, ask: float) -> QuoteTick:
        return QuoteTick(
            instrument_id=instrument_id,
            ts=int(at.timestamp() * 1e9),
            bid_price=bid,
            ask_price=ask,
            bid_size=1.0,
            ask_size=1.0,
        )

    utc = dt.timezone.utc
    frames = example._fold(
        [
            tick(dt.datetime(2025, 6, 2, 9, 15, 59, 500_000, tzinfo=utc), 100.0, 102.0),
            tick(dt.datetime(2025, 6, 2, 9, 16, 0, 250_000, tzinfo=utc), 110.0, 114.0),
            tick(dt.datetime(2025, 6, 2, 9, 16, 0, 750_000, tzinfo=utc), 106.0, 108.0),
        ]
    )
    assert [frame["date"] for frame in frames] == [
        "2025-06-02T09:15:00+00:00",
        "2025-06-02T09:16:00+00:00",
    ]
    first, second = frames
    assert (first["open"], first["close"], first["tick_count"]) == (101.0, 101.0, 1)
    # the second tick opens the frame at 112, the third moves the close down to 107
    assert (second["open"], second["high"], second["low"], second["close"]) == (
        112.0,
        112.0,
        107.0,
        107.0,
    )
    assert second["tick_count"] == 2


def test_subscription_and_connection_are_released(example):
    adapter = example._registry().create("fake")
    result = asyncio.run(example._stream(adapter, 3))
    assert adapter.is_connected() is False

    async def probe():
        await adapter.connect()
        try:
            with pytest.raises(AdapterError, match="unknown subscription"):
                await adapter.unsubscribe(result["subscription"]["id"])
        finally:
            await adapter.disconnect()

    asyncio.run(probe())


def test_output_is_deterministic_across_two_calls(example):
    first = json.dumps(example.run(), sort_keys=True)
    second = json.dumps(example.run(), sort_keys=True)
    assert first == second


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "frames.json"
    assert example.main(["--out", str(out)]) == 0
    stdout_json = json.loads(capsys.readouterr().out)
    assert json.loads(out.read_text()) == stdout_json
    assert stdout_json["frames"]


def test_main_accepts_a_custom_advance_count(example, capsys):
    assert example.main(["--advances", "5"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["advances"] == 5
    assert len(result["ticks"]) == 5


def test_main_happy_path_returns_zero(example, capsys):
    assert example.main([]) == 0
    assert json.loads(capsys.readouterr().out)["frames"]
