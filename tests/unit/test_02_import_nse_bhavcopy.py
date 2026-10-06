"""storage/02_import_nse_bhavcopy: dialect sniffing, clamping and a Parquet store round trip."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "storage" / "02_import_nse_bhavcopy.py"


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("import_nse_bhavcopy", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def report(example, tmp_path_factory):
    return example.run(tmp_path_factory.mktemp("bhavcopy"))


def _fixture(report, dialect):
    return next(f for f in report["fixtures"] if f["dialect"] == dialect)


def test_udiff_dialect_parses_clamps_and_drops_the_z_series(report):
    fx = _fixture(report, "udiff")
    assert fx["rows_parsed"] == 3
    assert fx["dropped_series"] == ["Z"]
    bars = {b["symbol"]: b for b in fx["bars"]}
    assert set(bars) == {"INFY", "RELIANCE", "TCS"}
    assert all(b["date"] == "2024-01-02" for b in fx["bars"])
    assert bars["RELIANCE"] == {
        "symbol": "RELIANCE",
        "date": "2024-01-02",
        "open": 2800.0,
        "high": 2850.0,
        "low": 2790.0,
        "close": 2840.0,
        "volume": 1000.0,
    }
    assert bars["TCS"]["high"] == 4010.0  # BE is a kept series
    assert bars["INFY"]["high"] == 1500.0  # clamped up from 1495.0, below its open
    assert bars["INFY"]["low"] == 1480.0


def test_sec_bhavdata_dialect_keeps_bz_and_drops_sm(report):
    fx = _fixture(report, "sec_bhavdata")
    assert fx["rows_parsed"] == 3
    assert fx["dropped_series"] == ["SM"]
    bars = {b["symbol"]: b for b in fx["bars"]}
    assert set(bars) == {"BAJAJ-AUTO", "HDFCBANK", "INFY"}
    assert all(b["date"] == "2024-01-03" for b in fx["bars"])
    assert bars["BAJAJ-AUTO"]["close"] == 3540.0  # BZ is a kept series
    assert bars["INFY"]["open"] == 1500.0


def test_legacy_dialect_clamps_a_negative_volume_to_zero(report):
    fx = _fixture(report, "legacy_bhavcopy")
    assert fx["rows_parsed"] == 3
    assert fx["dropped_series"] == ["A"]
    bars = {b["symbol"]: b for b in fx["bars"]}
    assert set(bars) == {"ITC", "SBIN", "TATASTEEL"}
    assert all(b["date"] == "2024-01-04" for b in fx["bars"])
    assert bars["TATASTEEL"]["volume"] == 0.0  # -200 clamped up to the invariant
    assert bars["SBIN"]["high"] == 648.0


def test_unrecognised_header_returns_an_empty_dict_and_the_report_records_it(example, report):
    assert example.parse_bhavcopy_csv(example.UNRECOGNISED_CSV) == {}
    assert report["unrecognised_header"] == {"raises": False, "result": {}}


def test_bars_are_reported_as_date_and_ohlcv_and_never_as_raw_ts(report):
    expected_keys = {"symbol", "date", "open", "high", "low", "close", "volume"}
    for fx in report["fixtures"]:
        assert fx["bars"], fx["dialect"]
        assert fx["notes"], fx["dialect"]
        for b in fx["bars"]:
            assert set(b) == expected_keys
            assert len(b["date"]) == 10


def test_one_result_set_round_trips_through_the_parquet_store(report):
    store = report["store"]
    assert store["appended_dialect"] == "udiff"
    assert store["window"] == {"start": "2024-01-02", "end": "2024-01-03"}
    assert store["stored"] == 3
    assert store["read"] == 3
    root = Path(store["data_dir"])
    assert "coverage_ledger.json" in store["files"]
    assert sum(name.endswith(".parquet") for name in store["files"]) == 3
    assert all((root / name).is_file() for name in store["files"])


def test_two_runs_agree_apart_from_the_store_path(example, tmp_path_factory):
    def strip(rep):
        return {**rep, "store": {k: v for k, v in rep["store"].items() if k != "data_dir"}}

    first = example.run(tmp_path_factory.mktemp("a"))
    second = example.run(tmp_path_factory.mktemp("b"))
    assert json.dumps(strip(first), sort_keys=True) == json.dumps(strip(second), sort_keys=True)


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "report.json"
    assert example.main(["--data-dir", str(tmp_path / "store"), "--out", str(out)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert json.loads(out.read_text()) == printed
    assert printed["store"]["stored"] == 3
    assert printed["store"]["read"] == 3


def test_main_happy_path_returns_zero(example, tmp_path, capsys):
    assert example.main(["--data-dir", str(tmp_path / "store")]) == 0
    assert "fixtures" in json.loads(capsys.readouterr().out)
