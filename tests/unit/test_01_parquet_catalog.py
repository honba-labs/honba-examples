"""storage/01_parquet_catalog: the store's on-disk layout, append contract and read window."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest
from honba.domain.instrument import InstrumentId
from honba.screener.coverage import DateInterval
from honba.screener.store import ParquetBarStore

from tests.synthetic import weekdays

_PATH = Path(__file__).resolve().parents[2] / "storage" / "01_parquet_catalog.py"
DAYS = weekdays(dt.date(2025, 12, 29), 5)  # one week that crosses the year partition
DATES = [day.isoformat() for day in DAYS]
SYMBOLS = ["RELIANCE", "TCS"]
FULL_WINDOW = {"start": DATES[0], "end": "2026-01-03"}


def _utc_date(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts / 1e9, tz=dt.timezone.utc).date().isoformat()


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("parquet_catalog", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def store_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("store01")


@pytest.fixture(scope="module")
def report(example, store_dir) -> dict:
    return example.run(store_dir)


def test_report_states_the_layout_the_ledger_the_reads_and_the_invariants(report):
    assert set(report) == {
        "data_dir",
        "layout",
        "files",
        "coverage",
        "read_back",
        "second_append_idempotent",
        "validation",
    }
    assert set(report["layout"]) == {"bars", "ledger", "example", "read_window"}
    assert report["layout"]["bars"] == "catalog/<TIMEFRAME>/<EXCHANGE>/<SYMBOL>/<year>.parquet"
    assert report["layout"]["ledger"] == "coverage_ledger.json"


def test_every_written_file_sits_under_the_documented_layout(report, store_dir):
    paths = [entry["path"] for entry in report["files"]]
    assert paths == sorted(paths)
    assert "coverage_ledger.json" in paths
    partitions = [path for path in paths if path != "coverage_ledger.json"]
    assert len(partitions) == len(SYMBOLS) * 2  # two symbols, two year partitions
    for path in partitions:
        parts = path.split("/")
        assert parts[:3] == ["catalog", "1D", "NSE"]
        assert parts[3] in SYMBOLS
        assert parts[4][:4] in {"2025", "2026"}
        assert parts[4].endswith(".parquet")
        assert (store_dir / path).is_file()


def test_reported_sizes_match_the_bytes_on_disk(report, store_dir):
    for entry in report["files"]:
        assert entry["bytes"] == (store_dir / entry["path"]).stat().st_size
        assert entry["bytes"] > 0


def test_read_back_returns_every_written_bar_in_chronological_order(report):
    assert [entry["instrument"]["symbol"] for entry in report["read_back"]] == SYMBOLS
    for entry in report["read_back"]:
        assert entry["instrument"]["exchange"] == "NSE"
        assert entry["count"] == len(DAYS)
        assert entry["dates"] == DATES
        assert entry["window"] == FULL_WINDOW


def test_the_report_agrees_with_a_store_reopened_from_disk(report, store_dir):
    store = ParquetBarStore(store_dir)
    interval = DateInterval(DAYS[0], DAYS[-1] + dt.timedelta(days=1))
    for symbol in SYMBOLS:
        bars = store.read(InstrumentId(symbol, "NSE"), "1D", interval)
        assert [_utc_date(bar.ts) for bar in bars] == DATES
        assert all(bar.volume > 0 for bar in bars)


def test_the_read_window_is_half_open_so_the_end_date_needs_plus_one_day(report):
    for entry in report["read_back"]:
        probe = entry["end_date_probe"]
        assert probe["window"] == {"start": DATES[0], "end": DATES[-1]}
        assert probe["count"] == len(DAYS) - 1  # the bar stamped on `end` is outside


def test_coverage_is_read_back_from_the_ledger(report):
    assert len(report["coverage"]) == len(SYMBOLS)
    for record, symbol in zip(report["coverage"], SYMBOLS, strict=True):
        assert record["exchange"] == "NSE"
        assert record["symbol"] == symbol
        assert record["timeframe"] == "1D"
        assert record["status"] == "final"
        assert record["source"] == "synthetic"
        assert record["interval"] == FULL_WINDOW
        assert record["row_count"] > 0


def test_appending_the_same_bars_again_changes_nothing_readable(report):
    assert report["second_append_idempotent"] is True


def test_validation_reports_the_bar_the_store_refuses(report):
    validation = report["validation"]
    assert validation["bar"]["low"] > validation["bar"]["open"]
    assert validation["error"].startswith("open must be between low and high")


def test_run_reports_the_data_dir_it_was_given(example, tmp_path):
    root = tmp_path / "elsewhere"
    assert Path(example.run(root)["data_dir"]) == root.resolve()
    assert (root / "coverage_ledger.json").is_file()


def test_output_is_deterministic_across_two_data_dirs(example, tmp_path):
    first = example.run(tmp_path / "a")
    second = example.run(tmp_path / "b")
    first.pop("data_dir")
    second.pop("data_dir")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "catalog.json"
    assert example.main(["--data-dir", str(tmp_path / "store"), "--out", str(out)]) == 0
    stdout_json = json.loads(capsys.readouterr().out)
    assert json.loads(out.read_text()) == stdout_json
    assert stdout_json["second_append_idempotent"] is True
    assert stdout_json["layout"]["ledger"] == "coverage_ledger.json"
