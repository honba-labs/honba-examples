"""tests/unit/test_09_alpha30_cross_sectional: Alpha 30 cross-sectional factor model."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import duckdb
import pytest

_PATH = (
    Path(__file__).resolve().parents[2] / "examples" / "universes" / "09_alpha30_cross_sectional.py"
)


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("alpha30_cross_sectional", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_symbol_parquet(
    nse_dir: Path, symbol: str, closes: list[float], days: list[dt.date]
) -> None:
    """Write one symbol's yearly parquet file the lakehouse layout expects."""
    sym_dir = nse_dir / symbol
    sym_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE b (ts BIGINT, open DOUBLE, high DOUBLE, low DOUBLE, "
        "close DOUBLE, volume DOUBLE)"
    )
    for day, close in zip(days, closes, strict=True):
        ts = (
            int(dt.datetime.combine(day, dt.time(9, 15), tzinfo=dt.timezone.utc).timestamp())
            * 1_000_000_000
        )
        con.execute(
            "INSERT INTO b VALUES (?, ?, ?, ?, ?, ?)",
            [ts, close, close + 0.5, close - 0.5, close, 1e6],
        )
    con.execute(f"COPY b TO '{sym_dir / '2024.parquet'}' (FORMAT PARQUET)")
    con.close()


def _synthetic_catalog(tmp_path: Path, symbols: list[str], asof: dt.date) -> Path:
    """``<tmp>/1D/NSE/<SYMBOL>/2024.parquet`` with bars rising through ``asof``."""
    nse_dir = tmp_path / "1D" / "NSE"
    days: list[dt.date] = []
    d = dt.date(2024, 1, 2)
    while d <= asof:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    assert len(days) >= 100, f"only {len(days)} sessions before {asof}"
    for i, symbol in enumerate(symbols):
        drift = 0.0004 * (i + 1)  # distinct personalities so factor ranking is meaningful
        closes = [100.0 * (1.0 + drift) ** j for j in range(len(days))]
        _write_symbol_parquet(nse_dir, symbol, closes, days)
    return nse_dir


def test_run_shape_and_summary(example, tmp_path: Path) -> None:
    nse_dir = _synthetic_catalog(tmp_path, ["AAA", "BBB", "CCC", "DDD"], dt.date(2024, 9, 30))
    result = example.run(
        catalog_dir=str(nse_dir),
        symbols=["AAA", "BBB", "CCC", "DDD"],
        start_date=dt.date(2024, 4, 1),
        end_date=dt.date(2024, 9, 30),
        initial_capital=1_000_000.0,
        top_decile=0.5,
        min_history_bars=5,
        fee=0.001,
    )
    assert set(result) >= {"config", "factors", "rebalances", "trades", "summary"}
    assert result["config"]["n_symbols"] == 4
    assert result["config"]["top_decile"] == 0.5
    assert result["summary"]["n_rebalances"] >= 1  # every 21 sessions
    summary = result["summary"]
    for key in (
        "final_equity",
        "total_return_pct",
        "cagr_pct",
        "sharpe",
        "max_drawdown_pct",
        "n_rebalances",
        "n_trades",
        "turnover",
    ):
        assert key in summary
    assert summary["n_trades"] >= 1


def test_top_decile_selection_and_rebalance_cadence(example, tmp_path: Path) -> None:
    nse_dir = _synthetic_catalog(tmp_path, ["AAA", "BBB", "CCC", "DDD"], dt.date(2024, 9, 30))
    result = example.run(
        catalog_dir=str(nse_dir),
        symbols=["AAA", "BBB", "CCC", "DDD"],
        start_date=dt.date(2024, 4, 1),
        end_date=dt.date(2024, 9, 30),
        top_decile=0.25,
        min_history_bars=5,
    )
    max_n = max(1, round(4 * 0.25))
    for rb in result["rebalances"]:
        assert 1 <= rb["n_selected"] <= max_n
        assert set(rb["selection"]) <= {"AAA", "BBB", "CCC", "DDD"}
        assert rb["trades"] >= 0
    dates = [rb["date"] for rb in result["rebalances"]]
    assert dates == sorted(dates)


def test_run_is_deterministic(example, tmp_path: Path) -> None:
    nse_dir = _synthetic_catalog(tmp_path, ["AAA", "BBB", "CCC"], dt.date(2024, 9, 30))
    kwargs = {
        "catalog_dir": str(nse_dir),
        "symbols": ["AAA", "BBB", "CCC"],
        "start_date": dt.date(2024, 5, 1),
        "end_date": dt.date(2024, 9, 30),
        "top_decile": 0.5,
        "min_history_bars": 5,
    }
    first = json.dumps(example.run(**kwargs), sort_keys=True, default=str)
    second = json.dumps(example.run(**kwargs), sort_keys=True, default=str)
    assert first == second


def test_empty_catalog_returns_flat_curve(example, tmp_path: Path) -> None:
    nse_dir = tmp_path / "1D" / "NSE"
    nse_dir.mkdir(parents=True)
    result = example.run(
        catalog_dir=str(nse_dir),
        symbols=["NOPE"],
        start_date=dt.date(2024, 4, 1),
        end_date=dt.date(2024, 9, 30),
    )
    assert result["rebalances"] == []
    assert result["trades"] == []
    assert result["summary"]["final_equity"] == 1_000_000.0


def test_main_writes_run_json_against_real_store(example, tmp_path, capsys) -> None:
    example.main(
        [
            "--start",
            "2023-01-02",
            "--end",
            "2024-12-31",
            "--out-dir",
            str(tmp_path / "out"),
            "--top-decile",
            "0.2",
        ]
    )
    run_json = tmp_path / "out" / "run.json"
    assert run_json.is_file()
    data = json.loads(run_json.read_text())
    assert data["run_hash"]
    assert data["config"]["top_decile"] == 0.2
    assert data["config"]["universe_name"] == "nifty50"
    assert 1 <= data["data_coverage"]["n_with_data"] <= 30
    assert data["result"]["n_rebalances"] >= 1
    out_text = capsys.readouterr().out
    assert "Alpha-30 cross-sectional" in out_text or "run_hash" in out_text
