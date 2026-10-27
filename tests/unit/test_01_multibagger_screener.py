"""tests/unit/test_01_multibagger_screener: multibagger screen with the QGLP fundamentals overlay."""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import duckdb
import pytest

from tests.unit.conftest import SCREEN_ASOF, write_symbol

_PATH = (
    Path(__file__).resolve().parents[2] / "examples" / "screening" / "01_multibagger_screener.py"
)


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("multibagger_screener", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fundamentals_db(tmp_path: Path) -> Path:
    """DuckDB with two FY fundamentals rows: one high-ROCE/low-leverage, one weak."""
    path = tmp_path / "fundamentals.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE companies (symbol VARCHAR, company_name VARCHAR)")
    con.execute(
        """CREATE TABLE financial_ratios (symbol VARCHAR, period_type VARCHAR, fiscal_date DATE,
        revenue DOUBLE, net_income DOUBLE, stockholders_equity DOUBLE, total_debt DOUBLE,
        operating_cash_flow DOUBLE, free_cash_flow DOUBLE, roce_pct DOUBLE, roe_pct DOUBLE,
        operating_margin_pct DOUBLE, net_margin_pct DOUBLE, debt_to_equity DOUBLE, eps DOUBLE)"""
    )
    con.execute(
        "CREATE TABLE financial_statements (symbol VARCHAR, period_type VARCHAR, "
        "statement_type VARCHAR, fiscal_date DATE, line_item VARCHAR, value DOUBLE, currency VARCHAR)"
    )
    con.execute(
        "INSERT INTO companies VALUES ('GOOD', 'Good Co'), ('BAD_ROCE', 'Bad Roce Co'), ('HIGH_DE', 'High De Co')"
    )
    # Fiscal year end well before SCREEN_ASOF - 90 days so it passes the lag cutoff.
    fy = dt.date(2024, 3, 1)  # period end well before the 90-day fiscal lag cutoff
    con.execute(
        "INSERT INTO financial_ratios VALUES (?, 'FY', ?, 1000, 100, 500, 50, 80, 70, 25.0, 20.0, 15.0, 12.0, 0.3, 5.0)",
        ["GOOD", fy],
    )
    con.execute(
        "INSERT INTO financial_ratios VALUES (?, 'FY', ?, 1000, 20, 500, 50, 80, 70, 5.0, 4.0, 3.0, 2.0, 0.3, 1.0)",
        ["BAD_ROCE", fy],
    )
    con.execute(
        "INSERT INTO financial_ratios VALUES (?, 'FY', ?, 1000, 100, 500, 500, 80, 70, 25.0, 20.0, 15.0, 12.0, 2.0, 5.0)",
        ["HIGH_DE", fy],
    )
    con.close()
    return path


def _rising_bars(nse_dir: Path, symbol: str, n: int = 300, asof: dt.date = SCREEN_ASOF) -> None:
    """The last ``n`` sessions on/before ``asof``, steadily rising: passes every momentum gate."""
    days = []
    d = asof
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= dt.timedelta(days=1)
    days = days[::-1]
    # Close 1 year before asof is ~60 percent lower -> min_return_1y gate passes;
    # close is near the high -> pct_from_high gate passes; SMA200 rises.
    closes = [100.0 + i * 0.3 for i in range(n)]
    bars = [
        (days[i], closes[i], closes[i] + 1.0, closes[i] - 1.0, closes[i], 1e6) for i in range(n)
    ]
    write_symbol(nse_dir, symbol, bars)


def _run(example, catalog_dir: Path, fundamentals: bool, asof: dt.date = SCREEN_ASOF) -> list[dict]:
    ex = example.MultibaggerScreenerExample(fundamentals=fundamentals)
    return ex.screen(catalog_dir, asof)


def test_fundamentals_overlay_keeps_only_strong_candidates(
    example, catalog_dir: Path, add_series, fundamentals_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from honba_examples.screening import momentum

    monkeypatch.setattr(momentum, "find_duckdb_path", lambda: fundamentals_db)
    nse_dir = catalog_dir
    for sym in ("GOOD", "BAD_ROCE", "HIGH_DE"):
        _rising_bars(nse_dir, sym, asof=SCREEN_ASOF)

    plain = _run(example, catalog_dir, fundamentals=False)
    plain_symbols = {c["symbol"] for c in plain}
    assert {"GOOD", "BAD_ROCE", "HIGH_DE"} <= plain_symbols

    gated = _run(example, catalog_dir, fundamentals=True)
    gated_symbols = {c["symbol"] for c in gated}
    assert gated_symbols == {"GOOD"}, gated_symbols
    assert gated[0]["symbol"] == "GOOD"
    assert gated[0]["roce_pct"] == 25.0


def test_fundamentals_flag_records_in_payload(
    example, catalog_dir: Path, add_series, monkeypatch: pytest.MonkeyPatch
) -> None:
    from honba_examples.screening import momentum

    monkeypatch.setattr(momentum, "find_duckdb_path", lambda: None)
    _rising_bars(catalog_dir, "GOOD", asof=SCREEN_ASOF)
    screener_cls = example.MultibaggerScreenerExample
    payload = screener_cls(fundamentals=True).json_payload(
        screener_cls(fundamentals=True).screen(catalog_dir, SCREEN_ASOF), SCREEN_ASOF
    )
    assert payload["fundamentals"] is True


def test_cli_fundamentals_flag_parses(example) -> None:
    ex = example.MultibaggerScreenerExample()
    args = ex.parse_args(["--fundamentals", "--asof", "2024-01-01", "--top", "3"])
    assert args.fundamentals is True
    assert args.top == 3


def test_main_happy_path_returns_zero(
    example, capsys, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from honba_examples.screening import momentum

    monkeypatch.setattr(momentum, "find_duckdb_path", lambda: None)
    _rising_bars(tmp_path / "cat" / "1D" / "NSE", "GOOD")
    cat = tmp_path / "cat" / "1D" / "NSE"
    _rising_bars(cat, "GOOD", asof=SCREEN_ASOF)
    assert (
        example.main(
            [
                "--data-dir",
                str(tmp_path / "cat"),
                "--asof",
                str(SCREEN_ASOF),
                "--format",
                "symbols",
                "--top",
                "2",
            ]
        )
        == 0
    )
