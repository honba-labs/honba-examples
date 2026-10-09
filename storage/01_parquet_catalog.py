"""01_parquet_catalog: the on-disk layout and read/write contract of ParquetBarStore.

ParquetBarStore is where a research workflow keeps the bars it trusts: one append-only
Parquet file per instrument, timeframe and year at
``<data_dir>/catalog/<TIMEFRAME>/<EXCHANGE>/<SYMBOL>/<year>.parquet``, plus a JSON ledger at
``<data_dir>/coverage_ledger.json`` recording which date ranges the store claims to hold.
``append(coverage_record, bars)`` is the only write path: it validates every bar
(``honba.screener.ports.validate_bar``), merges by ``ts`` so re-appending the same bars is
idempotent, and rewrites the ledger; ``read(instrument, timeframe, DateInterval(start, end))``
is a **half-open UTC window** ``[start 00:00, end 00:00)``, so a bar stamped on the end date
is only read when you ask for ``end + 1 day``; ``coverage`` reads the ledger back.

The example builds a two-instrument store from deterministic synthetic bars, reads it back
over the documented window, appends the same data a second time to show the idempotence, and
reports the store's refusal of a bar whose ``low`` sits above its ``open`` as data instead of
crashing. Nothing here touches the network or the honba checkout's own data root.

Run::

    python storage/01_parquet_catalog.py
    python storage/01_parquet_catalog.py --data-dir /tmp/store --out catalog.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from honba.adapters.errors import AdapterNotFound
from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.screener.coverage import CoverageRecord, CoverageStatus, DateInterval
from honba.screener.ports import validate_bar
from honba.screener.store import ParquetBarStore

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:  # plain checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba_examples.jsonable import jsonable
from tests.synthetic import bar, weekdays

_TIMEFRAME = "1D"
_DAYS = weekdays(dt.date(2025, 12, 29), 5)  # one week that crosses the year partition
_LAYOUT = {
    "bars": "catalog/<TIMEFRAME>/<EXCHANGE>/<SYMBOL>/<year>.parquet",
    "ledger": "coverage_ledger.json",
    "example": "catalog/1D/NSE/RELIANCE/2025.parquet",
    "read_window": (
        "DateInterval(start, end) reads the half-open UTC window [start 00:00, end 00:00): "
        "pass end + 1 day to read a bar stamped on `end`"
    ),
}
_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "output" / "01_parquet_catalog"


def _series() -> dict[str, list[Bar]]:
    """Two deterministic daily series, five sessions each, spanning the year partition."""
    return {
        "RELIANCE": [
            bar("RELIANCE", day, 2900.0 + 7 * index, close=2905.0 + 7 * index)
            for index, day in enumerate(_DAYS)
        ],
        "TCS": [
            bar("TCS", day, 1800.0 + 5 * index, close=1803.0 + 5 * index)
            for index, day in enumerate(_DAYS)
        ],
    }


def _window() -> DateInterval:
    """Every written day, closed by one day because the store's windows are half-open."""
    return DateInterval(_DAYS[0], _DAYS[-1] + dt.timedelta(days=1))


def _record(symbol: str, bars: list[Bar]) -> CoverageRecord:
    """What the ledger is told about one instrument: the full window, final, sourced."""
    return CoverageRecord(
        exchange=bars[0].instrument_id.exchange,
        symbol=symbol,
        timeframe=_TIMEFRAME,
        interval=_window(),
        status=CoverageStatus.FINAL,
        source="synthetic",
        row_count=len(bars),
    )


def _files(data_dir: Path) -> list[dict[str, Any]]:
    """Everything the store wrote, as paths relative to ``data_dir`` with their sizes."""
    return sorted(
        (
            {"path": str(path.relative_to(data_dir)), "bytes": path.stat().st_size}
            for path in data_dir.rglob("*")
            if path.is_file()
        ),
        key=lambda entry: entry["path"],
    )


def _read_back(store: ParquetBarStore, instrument: InstrumentId) -> dict[str, Any]:
    window = _window()
    bars = store.read(instrument, _TIMEFRAME, window)
    probe = DateInterval(_DAYS[0], _DAYS[-1])
    return {
        "instrument": {"symbol": instrument.symbol, "exchange": instrument.exchange},
        "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "count": len(bars),
        "dates": [_utc_date(bar.ts) for bar in bars],
        "end_date_probe": {
            "window": {"start": probe.start.isoformat(), "end": probe.end.isoformat()},
            "count": len(store.read(instrument, _TIMEFRAME, probe)),
        },
    }


def _validation(sample: list[Bar]) -> dict[str, Any]:
    """The store's refusal of a bar whose ``low`` sits above its ``open``, reported as data."""
    good = sample[0]
    bad = Bar(
        instrument_id=good.instrument_id,
        ts=good.ts,
        open=good.open,
        high=good.high,
        low=good.open + 1.0,
        close=good.close,
        volume=good.volume,
    )
    try:
        validate_bar(bad)
    except ValueError as exc:
        error = str(exc)
    else:
        error = None
    return {
        "bar": {
            "open": bad.open,
            "high": bad.high,
            "low": bad.low,
            "close": bad.close,
            "volume": bad.volume,
        },
        "error": error,
    }


def _utc_date(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts / 1e9, tz=dt.timezone.utc).date().isoformat()


def run(data_dir: Path | None = None) -> dict[str, Any]:
    """Write the sample store twice and report its layout, ledger, reads and invariants.

    The report has seven keys: ``data_dir``, ``layout``, ``files``, ``coverage``,
    ``read_back``, ``second_append_idempotent`` and ``validation``.

    Everything is appended twice - identical record, identical bars - so
    ``second_append_idempotent`` can compare the read-back before and after the second pass.
    Bar sets dedupe by ``ts``; the ledger merges the duplicate interval into a single record
    whose ``row_count`` is the sum of both appends, because it counts rows appended, not
    distinct bars held.
    """
    store = ParquetBarStore(Path(data_dir) if data_dir is not None else _DEFAULT_DATA_DIR)
    series = _series()
    instruments = {symbol: bars[0].instrument_id for symbol, bars in series.items()}
    records = {symbol: _record(symbol, bars) for symbol, bars in series.items()}

    for symbol in sorted(series):
        store.append(records[symbol], series[symbol])
    before = {symbol: store.read(instruments[symbol], _TIMEFRAME, _window()) for symbol in series}
    for symbol in sorted(series):
        store.append(records[symbol], series[symbol])
    after = {symbol: store.read(instruments[symbol], _TIMEFRAME, _window()) for symbol in series}

    report = {
        "data_dir": str(store.data_dir),
        "layout": dict(_LAYOUT),
        "files": _files(store.data_dir),
        "coverage": [
            jsonable(record)
            for symbol in sorted(series)
            for record in store.coverage(instruments[symbol], _TIMEFRAME)
        ],
        "read_back": [_read_back(store, instruments[symbol]) for symbol in sorted(series)],
        "second_append_idempotent": all(before[symbol] == after[symbol] for symbol in series),
        "validation": _validation(series["RELIANCE"]),
    }
    return jsonable(report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="store root (default: <repo>/output/01_parquet_catalog)",
    )
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON here")
    args = parser.parse_args(argv)

    try:
        result = run(args.data_dir)
    except (ValueError, AdapterNotFound) as exc:
        raise SystemExit(str(exc))

    text = json.dumps(result, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
