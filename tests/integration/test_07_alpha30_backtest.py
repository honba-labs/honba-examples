"""07 end to end: catalog strategy -> Parquet store -> runner -> next-open fills -> run.json.

Synthetic, deterministic daily bars for the Alpha-30 universe are written to a
temporary Parquet store; no network and no real market data. Needs the sibling
``honba-strategies`` checkout (or ``$HONBA_STRATEGIES_DIR``) for the strategy.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import math
from pathlib import Path
from types import ModuleType

import pytest
from honba.markets.india.universes import resolve_universe
from honba.screener.coverage import CoverageRecord, CoverageStatus, DateInterval
from honba.screener.store import ParquetBarStore

from honba_examples.catalog import CatalogError, find_catalog
from tests.synthetic import bar, weekdays

SCRIPT = Path(__file__).resolve().parents[2] / "universes" / "07_alpha30_backtest.py"
DAYS = weekdays(dt.date(2026, 5, 4), 45)  # ~9 weeks of sessions
WARMUP_START = DAYS[0]
TEST_START = DAYS[10]
TEST_END = DAYS[-1]
MISSING = "IDEA"  # one member deliberately has no bars


def _catalog_or_skip() -> Path:
    try:
        return find_catalog()
    except CatalogError as exc:
        pytest.skip(str(exc))


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ex07", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def store_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("store")
    (root / "catalog").mkdir()
    store = ParquetBarStore(root)
    members = resolve_universe("nifty200_alpha_30", exchange="NSE")
    for k, iid in enumerate(members):
        if iid.symbol == MISSING:
            continue
        base = 100.0 + 37.0 * k
        bars = []
        for i, d in enumerate(DAYS):
            drift = base * (1 + 0.002 * i * (1 if k % 3 else -1) + 0.01 * math.sin(i + k))
            bars.append(bar(iid.symbol, d, round(drift, 2), close=round(drift * 1.003, 2)))
        record = CoverageRecord(
            exchange=iid.exchange,
            symbol=iid.symbol,
            timeframe="1D",
            interval=DateInterval(DAYS[0], DAYS[-1] + dt.timedelta(days=1)),
            status=CoverageStatus.FINAL,
            source="synthetic",
            row_count=len(bars),
        )
        store.append(record, bars)
    return root


def _run(store_dir: Path, out: Path, *extra: str) -> dict:
    catalog = _catalog_or_skip()
    _load_script().main(
        [
            "--data-dir",
            str(store_dir),
            "--out-dir",
            str(out),
            "--strategies-dir",
            str(catalog),
            "--test-start",
            TEST_START.isoformat(),
            "--test-end",
            TEST_END.isoformat(),
            *extra,
        ]
    )
    return json.loads((out / "run.json").read_text())


def test_run_writes_complete_structured_output(store_dir: Path, tmp_path: Path) -> None:
    run = _run(store_dir, tmp_path / "out")
    for key in ("config", "universe", "data_coverage", "result", "run_hash", "input_hash"):
        assert key in run
    cfg = run["config"]
    assert cfg["test_start"] == TEST_START.isoformat()
    assert cfg["test_end"] == TEST_END.isoformat()
    assert cfg["fill_model"] == "next_session_open"
    assert cfg["settlement_days"] == 2  # NSE delivery, from the engine market pack
    assert isinstance(cfg["capital_paise"], int)
    assert len(run["universe"]) == 30
    assert run["data_coverage"]["missing"] == [MISSING]

    result = run["result"]
    curve = result["equity_curve"]
    assert curve[0]["date"] == TEST_START.isoformat()
    assert curve[-1]["date"] == TEST_END.isoformat()
    fills = result["fills"]
    assert fills, "the strategy should have invested"
    assert min(f["date"] for f in fills) > TEST_START.isoformat()  # next-open, never same bar
    assert all(f["submitted_date"] < f["date"] for f in fills)
    assert all(isinstance(f["cost_paise"], int) and f["cost_paise"] > 0 for f in fills)
    assert all(f["symbol"] != MISSING for f in fills)
    m = result["metrics"]
    assert m["total_fees_paise"] == sum(f["cost_paise"] for f in fills)
    assert m["n_fills"] == len(fills)
    assert m["final_equity_paise"] == curve[-1]["equity_paise"]
    assert m["n_rejected_intents"] == 0


def test_warmup_does_not_trade_or_leak_into_results(store_dir: Path, tmp_path: Path) -> None:
    run = _run(store_dir, tmp_path / "out", "--warmup-days", "10")
    result = run["result"]
    assert run["config"]["warmup_start"] < TEST_START.isoformat()
    assert result["warmup_sessions"] > 0
    assert all(f["date"] > TEST_START.isoformat() for f in result["fills"])
    assert result["equity_curve"][0]["date"] == TEST_START.isoformat()
    assert result["metrics"]["n_suppressed_warmup"] > 0


def test_same_inputs_give_the_same_run_hash(store_dir: Path, tmp_path: Path) -> None:
    a = _run(store_dir, tmp_path / "a")
    b = _run(store_dir, tmp_path / "b")
    assert a["run_hash"] == b["run_hash"]
    assert a["result"] == b["result"]


def test_require_full_coverage_fails_on_missing_members(store_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match=MISSING):
        _run(store_dir, tmp_path / "out", "--require-full-coverage")
