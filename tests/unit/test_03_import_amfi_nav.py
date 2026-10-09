"""storage/03_import_amfi_nav: NAVAll.txt parsed, appended and read back offline."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest
from honba.domain.instrument import InstrumentId
from honba.screener.coverage import DateInterval
from honba.screener.store import ParquetBarStore

from honba_examples.amfi import parse_navall
from tests.synthetic import session_ts

_PATH = Path(__file__).resolve().parents[2] / "storage" / "03_import_amfi_nav.py"
D1 = "2025-06-02"
D2 = "2025-06-03"
CODES = ["100001", "100002", "100003"]


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("import_amfi_nav", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def store_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("store03")


@pytest.fixture(scope="module")
def report(example, store_dir) -> dict:
    return example.run(store_dir)


def _utc_date(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts / 1e9, tz=dt.timezone.utc).date().isoformat()


def test_report_states_the_import_shape(report):
    assert set(report) == {
        "data_dir",
        "sources",
        "rows_parsed",
        "rows_skipped",
        "structural_damage",
        "window",
        "files",
        "read_back",
        "schemes",
        "ohlc_note",
    }
    assert report["window"] == {"start": D1, "end": "2025-06-04"}  # union, half-open


def test_rows_parsed_and_the_missing_nav_is_reported_with_its_reason(report):
    assert report["rows_parsed"] == 5
    assert report["sources"] == [
        {"source": "NAVALL_FIRST_TXT", "rows": 2, "skipped": 1},
        {"source": "NAVALL_SECOND_TXT", "rows": 3, "skipped": 0},
    ]
    assert len(report["rows_skipped"]) == 1
    (skip,) = report["rows_skipped"]
    assert skip["source"] == "NAVALL_FIRST_TXT"
    assert skip["line"] == 5
    assert skip["code"] == "100003"
    assert "'-'" in skip["reason"]


def test_a_malformed_row_raises_value_error_from_the_parser(example):
    with pytest.raises(ValueError) as excinfo:
        parse_navall(example.MALFORMED_NAVALL_TXT)
    message = str(excinfo.value)
    assert "line 4" in message
    assert "88.9000" in message


def test_the_damaged_publication_is_reported_as_data_not_a_crash(report):
    damage = report["structural_damage"]
    assert damage["source"] == "MALFORMED_NAVALL_TXT"
    assert "line 4" in damage["error"]
    assert "88.9000" in damage["error"]


def test_read_back_covers_the_union_window_of_both_publications(report):
    counts = {entry["code"]: entry["count"] for entry in report["read_back"]}
    assert set(counts) == set(CODES)
    assert counts == {"100001": 2, "100002": 2, "100003": 1}
    assert sum(counts.values()) == report["rows_parsed"]
    for entry in report["read_back"]:
        assert entry["dates"] == sorted(entry["dates"])
        assert set(entry["dates"]) <= {D1, D2}
        assert entry["flat"] is True  # every bar: open == high == low == close == NAV


def test_files_are_written_under_the_data_dir_with_the_amfi_exchange(report, store_dir):
    paths = [entry["path"] for entry in report["files"]]
    assert "coverage_ledger.json" in paths
    assert len(paths) == len(CODES) + 1
    for entry in report["files"]:
        assert entry["bytes"] == (store_dir / entry["path"]).stat().st_size
        if entry["path"] == "coverage_ledger.json":
            continue
        parts = entry["path"].split("/")
        assert parts[:3] == ["catalog", "1D", "AMFI"]
        assert parts[3] in CODES
        assert parts[4] == "2025.parquet"
        assert (store_dir / entry["path"]).is_file()


def test_every_scheme_reports_its_series_and_change(report):
    schemes = {scheme["code"]: scheme for scheme in report["schemes"]}
    assert set(schemes) == set(CODES)
    growth = schemes["100001"]
    assert growth["name"] == "Sample Flexi Cap Fund - Growth"
    assert growth["dates"] == [D1, D2]
    assert growth["navs"] == [123.4567, 124.1]
    assert growth["first"] == 123.4567 and growth["last"] == 124.1
    assert growth["change_pct"] == round((124.1 - 123.4567) / 123.4567 * 100, 4)
    assert growth["change_pct"] > 0
    assert schemes["100002"]["change_pct"] < 0
    assert schemes["100002"]["navs"] == [89.1011, 88.75]
    assert schemes["100003"]["dates"] == [D2]  # its first NAV was the missing one
    assert schemes["100003"]["change_pct"] == 0.0


def test_the_stored_nav_bars_are_flat_and_stamped_at_the_session_open(report, store_dir):
    store = ParquetBarStore(store_dir)
    window = DateInterval(dt.date(2025, 6, 2), dt.date(2025, 6, 4))
    bars = store.read(InstrumentId("100001", "AMFI"), "1D", window)
    assert [_utc_date(bar.ts) for bar in bars] == [D1, D2]
    for bar in bars:
        assert bar.open == bar.high == bar.low == bar.close
        assert bar.volume == 0.0
        assert bar.ts == session_ts(dt.date.fromisoformat(_utc_date(bar.ts)))


def test_the_report_says_why_a_nav_bar_has_no_ohlc(report):
    assert "open = high = low = close" in report["ohlc_note"]
    assert "volume" in report["ohlc_note"]


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
    out = tmp_path / "nav.json"
    assert example.main(["--data-dir", str(tmp_path / "store"), "--out", str(out)]) == 0
    stdout_json = json.loads(capsys.readouterr().out)
    assert json.loads(out.read_text()) == stdout_json
    assert stdout_json["rows_parsed"] == 5
    assert stdout_json["window"]["end"] == "2025-06-04"
