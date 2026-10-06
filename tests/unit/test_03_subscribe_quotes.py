"""basic/03_subscribe_quotes: stream quotes through the fake adapter, offline and deterministic."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from honba.adapters.errors import AdapterError
from honba.adapters.models import StreamMode
from honba.domain.instrument import InstrumentId

_PATH = Path(__file__).resolve().parents[2] / "basic" / "03_subscribe_quotes.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("subscribe_quotes", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def report(example):
    return example.run(adapter="fake")


def test_result_shape_reports_subscription_ticks_snapshot_and_refusals(report):
    assert set(report) == {
        "adapter",
        "advances",
        "subscription",
        "ticks",
        "snapshot",
        "unsupported",
    }
    assert report["adapter"] == "fake"
    subscription = report["subscription"]
    assert subscription["id"].startswith("sub-")
    assert subscription["mode"] == "quote"
    assert [i["symbol"] for i in subscription["instruments"]] == ["RELIANCE", "TCS"]
    assert set(report["snapshot"]) == {
        "symbol",
        "exchange",
        "ts",
        "bid_price",
        "ask_price",
        "bid_size",
        "ask_size",
        "mid_price",
    }
    snapshot = report["snapshot"]
    assert snapshot["mid_price"] == (snapshot["bid_price"] + snapshot["ask_price"]) / 2


def test_one_tick_per_instrument_per_advance(report):
    ticks = report["ticks"]
    assert len(ticks) == report["advances"] * len(report["subscription"]["instruments"])
    assert [t["symbol"] for t in ticks] == ["RELIANCE", "TCS", "RELIANCE", "TCS"]
    for tick in ticks:
        assert json.loads(json.dumps(tick)) == tick
        assert {"symbol", "bid_price", "ask_price"} <= set(tick)
        assert tick["bid_price"] <= tick["ask_price"]
        assert tick["bid_size"] > 0 and tick["ask_size"] > 0
    stamps = [t["ts"] for t in ticks]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps)


def test_ticks_and_snapshot_follow_the_fake_price_series(report):
    # advance_prices moves to the next (bid, ask) pair *before* pushing, so the first
    # pushed RELIANCE quote is the series' second pair, never its first (2450.45, 2450.55).
    reliance = [t for t in report["ticks"] if t["symbol"] == "RELIANCE"]
    tcs = [t for t in report["ticks"] if t["symbol"] == "TCS"]
    assert (reliance[0]["bid_price"], reliance[0]["ask_price"]) == (2451.75, 2452.10)
    assert (reliance[1]["bid_price"], reliance[1]["ask_price"]) == (2452.60, 2452.85)
    assert (tcs[0]["bid_price"], tcs[0]["ask_price"]) == (4118.90, 4120.00)
    # the one-shot snapshot is taken where the stream stopped, so it agrees with the last
    # tick pushed for that instrument
    snapshot = report["snapshot"]
    assert (snapshot["bid_price"], snapshot["ask_price"]) == (2452.60, 2452.85)


def test_output_is_deterministic_json(example):
    first = json.dumps(example.run(adapter="fake"), sort_keys=True)
    second = json.dumps(example.run(adapter="fake"), sort_keys=True)
    assert first == second


def test_unsubscribe_releases_the_handle(example):
    adapter = example._registry().create("fake")
    result = asyncio.run(example._stream(adapter))
    subscription_id = result["subscription"]["id"]

    async def probe():
        await adapter.connect()
        try:
            with pytest.raises(AdapterError, match="unknown subscription"):
                await adapter.unsubscribe(subscription_id)
        finally:
            await adapter.disconnect()

    asyncio.run(probe())


def test_depth_is_reported_as_unsupported_instead_of_raising(report):
    reason = report["unsupported"]["depth"]
    assert "does not support" in reason
    assert "depth" in reason


def test_subscribe_for_an_unknown_instrument_raises_adapter_error(example):
    adapter = example._registry().create("fake")

    async def scenario():
        await adapter.connect()
        try:
            with pytest.raises(AdapterError, match="unknown instrument"):
                await adapter.subscribe(
                    instruments=(InstrumentId("INFY", "NSE"),),
                    mode=StreamMode.QUOTE,
                    callback=lambda event: None,
                )
        finally:
            await adapter.disconnect()

    asyncio.run(scenario())


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "quotes.json"
    assert example.main(["--adapter", "fake", "--out", str(out)]) == 0
    stdout_json = json.loads(capsys.readouterr().out)
    assert json.loads(out.read_text()) == stdout_json
    assert stdout_json["ticks"]
