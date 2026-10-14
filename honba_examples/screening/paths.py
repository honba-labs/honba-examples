"""Catalog / DuckDB path discovery and latest market date lookup."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


def find_catalog_path(explicit_path: Path | None = None) -> Path:
    """Locate the 1D NSE Parquet catalog directory."""
    if explicit_path and explicit_path.is_dir():
        return explicit_path

    candidates = [
        Path.cwd() / "honba-data" / "data" / "catalog" / "1D" / "NSE",
        Path.cwd() / "data" / "catalog" / "1D" / "NSE",
        Path(__file__).resolve().parents[3] / "honba-data" / "data" / "catalog" / "1D" / "NSE",
        Path(__file__).resolve().parents[3] / "data" / "catalog" / "1D" / "NSE",
    ]
    for c in candidates:
        if c.is_dir():
            return c.resolve()

    raise FileNotFoundError(
        "Could not find 1D NSE catalog. Please pass --catalog-dir pointing to <data_dir>/catalog/1D/NSE."
    )


def find_mutual_funds_db_path() -> Path | None:
    """Optionally locate mutual_funds.duckdb for mutual fund screening."""
    candidates = [
        Path.cwd() / "honba-data" / "data" / "mutual_funds.duckdb",
        Path.cwd() / "data" / "mutual_funds.duckdb",
        Path(__file__).resolve().parents[3] / "honba-data" / "data" / "mutual_funds.duckdb",
        Path(__file__).resolve().parents[3] / "data" / "mutual_funds.duckdb",
    ]
    for c in candidates:
        if c.is_file():
            return c.resolve()
    return None


def find_duckdb_path() -> Path | None:
    """Optionally locate fundamentals.duckdb for fundamental ratio overlay."""
    candidates = [
        Path.cwd() / "honba-data" / "data" / "fundamentals.duckdb",
        Path.cwd() / "data" / "fundamentals.duckdb",
        Path(__file__).resolve().parents[3] / "honba-data" / "data" / "fundamentals.duckdb",
        Path(__file__).resolve().parents[3] / "data" / "fundamentals.duckdb",
    ]
    for c in candidates:
        if c.is_file():
            return c.resolve()
    return None


def get_latest_market_date(catalog_dir: Path) -> dt.date:
    """Determine the latest trading session date quickly without full catalog scans."""
    try:
        import duckdb

        # Check prominent liquid stock directories first for instant date resolution
        check_symbols = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN"]
        for sym in check_symbols:
            sym_dir = catalog_dir / sym
            if sym_dir.is_dir():
                parquets = sorted(sym_dir.glob("*.parquet"))
                if parquets:
                    res = duckdb.execute(
                        f"SELECT max(epoch_ms(ts // 1000000)::DATE) FROM read_parquet('{parquets[-1]}')"
                    ).fetchone()
                    if res and res[0]:
                        return res[0]

        # Fallback: inspect any single parquet file
        any_parquet = next(catalog_dir.glob("*/*.parquet"), None)
        if any_parquet:
            res = duckdb.execute(
                f"SELECT max(epoch_ms(ts // 1000000)::DATE) FROM read_parquet('{any_parquet}')"
            ).fetchone()
            if res and res[0]:
                return res[0]
    except Exception as exc:  # noqa: BLE001
        logger.debug("Falling back on system date: %s", exc)

    return dt.datetime.now(dt.timezone.utc).date()


def resolve_eval_window(
    asof_str: str | None,
    start_date: dt.date,
    end_date_str: str | None,
    end_date: dt.date | None,
    latest_data_date: dt.date,
    notify: Callable[[str], None] | None = None,
) -> tuple[dt.date, dt.date]:
    """Resolve the in-sample ``asof`` date and the out-of-sample terminal date.

    ``asof`` falls back to ``start_date`` and is snapped to ``latest_data_date`` when it lies
    beyond the catalog (``notify`` receives a note). The terminal date prefers ``--end-date``,
    then ``end_date`` if the catalog covers it, else the latest catalog date.
    Raises ``ValueError`` with a user-facing message on a malformed date string.
    """
    if asof_str:
        try:
            asof = dt.date.fromisoformat(asof_str)
        except ValueError:
            raise ValueError(f"Invalid date format for --asof: '{asof_str}'") from None
    else:
        asof = start_date

    if asof > latest_data_date:
        if notify is not None:
            notify(
                f"Note: Specified --asof date ({asof}) is beyond latest catalog date ({latest_data_date}). "
                f"Snapping evaluation date to {latest_data_date}."
            )
        asof = latest_data_date

    if end_date_str:
        try:
            end = dt.date.fromisoformat(end_date_str)
        except ValueError:
            raise ValueError(f"Invalid date format for --end-date: '{end_date_str}'") from None
    elif end_date and end_date <= latest_data_date:
        end = end_date
    else:
        end = latest_data_date
    return asof, end
