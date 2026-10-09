"""storage/04_export_tearsheet: a deterministic synthetic backtest exported as JSON and Markdown."""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from honba_examples.backtest import canonical_hash
from tests.synthetic import weekdays

_PATH = Path(__file__).resolve().parents[2] / "storage" / "04_export_tearsheet.py"
DAYS = weekdays(dt.date(2026, 6, 1), 10)


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("export_tearsheet", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def exported(example, tmp_path_factory):
    return example.run(tmp_path_factory.mktemp("tearsheet"))


@pytest.fixture(scope="module")
def doc(exported):
    return json.loads((Path(exported["out_dir"]) / "tearsheet.json").read_text(encoding="utf-8"))


def test_json_carries_the_schema_the_metrics_and_the_equity_curve(exported, doc):
    assert doc["schema"] == "honba-examples/tearsheet/v1"
    metrics = doc["run"]["metrics"]
    for name in (
        "final_value",
        "total_return_pct",
        "cagr_pct",
        "max_drawdown_pct",
        "sharpe",
        "turnover",
        "avg_cash_pct",
        "n_fills",
        "total_fees_minor",
    ):
        assert name in metrics
    curve = doc["run"]["equity_curve"]
    assert [p["date"] for p in curve] == [d.isoformat() for d in DAYS[3:]]
    assert all(isinstance(p["equity_minor"], int) for p in curve)
    assert exported["metrics"] == metrics


def test_hash_covers_the_run_payload_and_is_reported(exported, doc):
    assert doc["canonical_hash"] == canonical_hash(doc["run"])
    assert exported["hash"] == doc["canonical_hash"]
    assert len(exported["hash"]) == 64


def test_backtest_buys_once_and_sells_once(doc):
    run = doc["run"]
    assert run["metrics"]["n_fills"] == 2
    assert [f["side"] for f in run["fills"]] == ["buy", "sell"]
    assert run["metrics"]["n_rejected_intents"] == 0
    assert run["metrics"]["n_unfilled_at_end"] == 0
    assert run["final_positions"] == {}


def test_markdown_lists_every_metric_and_both_equity_endpoints(exported, doc):
    md = (Path(exported["out_dir"]) / "tearsheet.md").read_text(encoding="utf-8")
    assert md.startswith("# Tearsheet")
    for name in exported["metrics"]:
        assert name in md
    curve = doc["run"]["equity_curve"]
    assert curve[0]["date"] in md
    assert curve[-1]["date"] in md
    assert str(curve[0]["equity_minor"]) in md
    assert str(curve[-1]["equity_minor"]) in md
    assert f"Fills: {doc['run']['metrics']['n_fills']}" in md


def test_exported_bytes_are_identical_across_two_runs(example, tmp_path):
    first = example.run(tmp_path / "a")
    second = example.run(tmp_path / "b")
    for name in ("tearsheet.json", "tearsheet.md"):
        a = (Path(first["out_dir"]) / name).read_bytes()
        b = (Path(second["out_dir"]) / name).read_bytes()
        assert a == b, name


def test_reported_sha256_matches_the_file_bytes(exported):
    root = Path(exported["out_dir"])
    assert [f["name"] for f in exported["files"]] == ["tearsheet.json", "tearsheet.md"]
    for entry in exported["files"]:
        payload = (root / entry["name"]).read_bytes()
        assert entry["bytes"] == len(payload)
        assert entry["sha256"] == hashlib.sha256(payload).hexdigest()


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys):
    out = tmp_path / "report.json"
    assert example.main(["--out-dir", str(tmp_path / "out"), "--out", str(out)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert json.loads(out.read_text()) == printed
    assert [f["name"] for f in printed["files"]] == ["tearsheet.json", "tearsheet.md"]


def test_main_happy_path_returns_zero(example, tmp_path, capsys):
    assert example.main(["--out-dir", str(tmp_path / "out")]) == 0
    assert "hash" in json.loads(capsys.readouterr().out)
