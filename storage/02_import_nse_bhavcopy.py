"""02_import_nse_bhavcopy: parse an exchange EOD CSV into Bars and hand them to the Parquet store.

The ingest edge is deliberately thin: ``honba.data.loaders.parse_bhavcopy_csv``
sniffs the header dialect of an exchange's end-of-day CSV and returns
``dict[str, Bar]`` - offline and pure, no network, no store. This example embeds
one small fixture per dialect it accepts (UDiFF, Sec Bhavdata, legacy bhavcopy),
reports what came out of each, and then round-trips the UDiFF result set through
a ``ParquetBarStore`` under ``output/`` (never the honba data root).

What the parser does, asserted here:

* only the ``EQ``, ``BE`` and ``BZ`` series survive; any other series is dropped
  before the row is read, and the report's ``dropped_series`` shows which;
* ``high`` is clamped up to at least ``max(open, close)``, ``low`` down to
  ``min(open, close)`` and a negative volume to ``0`` - exactly the invariants
  ``ParquetBarStore.append`` validates, so an anomalous exchange row still
  stores;
* the date column (``TradDt`` / ``DATE1`` / ``TIMESTAMP``) is parsed as
  ``%Y-%m-%d``, ``%d-%b-%Y``, ``%d-%m-%Y`` or ``%Y%m%d``. A missing or
  unparseable date falls back to ``date.today()`` (the wall clock) and the bar's
  ``ts`` is stamped from a *naive* 09:15 datetime, so it depends on the machine's
  timezone - therefore this report and its tests carry the parsed date and
  OHLCV, never a raw ``ts``;
* an unrecognised header is not an error: the parser returns an empty dict
  instead of raising (verified by running it, recorded in the report).

Run::

    python storage/02_import_nse_bhavcopy.py
    python storage/02_import_nse_bhavcopy.py --data-dir /tmp/bhavcopy --out report.json
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import sys
from pathlib import Path
from typing import Any

from honba.data.loaders import parse_bhavcopy_csv
from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.screener.coverage import CoverageRecord, CoverageStatus, DateInterval
from honba.screener.store import ParquetBarStore

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:  # plain checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba_examples.base import ts_to_date
from honba_examples.jsonable import jsonable

EXCHANGE = "NSE"
TIMEFRAME = "1D"
SOURCE = "parse_bhavcopy_csv"
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "output" / "02_import_nse_bhavcopy"

UDIFF_CSV = """\
TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradQty
2024-01-02,RELIANCE,EQ,2800.00,2850.00,2790.00,2840.00,1000
2024-01-02,TCS,BE,4000.00,4010.00,3980.00,4005.00,500
2024-01-02,WIPRO,Z,500.00,510.00,495.00,505.00,200
2024-01-02,INFY,EQ,1500.00,1495.00,1480.00,1490.00,300
"""

SEC_BHAVDATA_CSV = """\
DATE1,SYMBOL,SERIES,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,CLOSE_PRICE,TTL_TRD_QNTY
03-01-2024,INFY,EQ,1500.00,1520.00,1495.00,1510.00,700
03-01-2024,HDFCBANK,EQ,1600.00,1610.00,1590.00,1605.00,450
03-01-2024,BAJAJ-AUTO,BZ,3500.00,3550.00,3480.00,3540.00,150
03-01-2024,IDEA,SM,12.50,13.00,12.00,12.80,9000
"""

LEGACY_CSV = """\
TIMESTAMP,SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,TOTTRDQTY
04-01-2024,SBIN,EQ,640.00,648.00,635.00,645.00,5000
04-01-2024,ITC,BE,460.00,463.00,458.00,461.00,2500
04-01-2024,NIFTY50,A,21000.00,21100.00,20950.00,21050.00,100
04-01-2024,TATASTEEL,EQ,130.00,131.00,128.00,129.50,-200
"""

UNRECOGNISED_CSV = "Foo,Bar,Baz\n1,2,3\n"

FIXTURES: tuple[tuple[str, str], ...] = (
    ("udiff", UDIFF_CSV),
    ("sec_bhavdata", SEC_BHAVDATA_CSV),
    ("legacy_bhavcopy", LEGACY_CSV),
)

NOTES = {
    "udiff": (
        "UDiFF header (TradDt/TckrSymb/SctySrs), date parsed as %Y-%m-%d; series Z dropped, "
        "BE kept; INFY high 1495.0 clamped up to its open 1500.0"
    ),
    "sec_bhavdata": (
        "Sec Bhavdata header (DATE1/SYMBOL/SERIES), date parsed as %d-%m-%Y; series SM "
        "dropped, BZ kept"
    ),
    "legacy_bhavcopy": (
        "Legacy header (TIMESTAMP/SYMBOL/SERIES), date parsed as %d-%m-%Y; series A dropped; "
        "TATASTEEL volume -200 clamped to 0.0"
    ),
}


def _dropped_series(csv_content: str, bars: dict[str, Bar]) -> list[str]:
    """Series codes whose rows did not become bars (in practice: not EQ/BE/BZ)."""
    reader = csv.DictReader(io.StringIO(csv_content))
    field_map = {name.strip().upper(): name for name in reader.fieldnames or [] if name}
    series_col = field_map.get("SCTYSRS") or field_map.get("SERIES")
    symbol_col = field_map.get("TCKRSYMB") or field_map.get("SYMBOL")
    if series_col is None or symbol_col is None:
        return []
    return sorted(
        {
            (row.get(series_col) or "").strip().upper()
            for row in reader
            if (row.get(symbol_col) or "").strip() not in bars
        }
    )


def _bar_rows(bars: dict[str, Bar]) -> list[dict[str, Any]]:
    """Bars as their stable image: date and OHLCV only, never the timezone-dependent ts."""
    return [
        {
            "symbol": symbol,
            "date": ts_to_date(bar.ts).isoformat(),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        for symbol, bar in sorted(bars.items())
    ]


def _store_round_trip(root: Path, bars: dict[str, Bar]) -> dict[str, Any]:
    """Append every parsed bar, read the window back, and list the files written."""
    store = ParquetBarStore(root)
    day = min(ts_to_date(bar.ts) for bar in bars.values())
    window = DateInterval(day, day + dt.timedelta(days=1))
    for symbol, bar in sorted(bars.items()):
        store.append(
            CoverageRecord(
                exchange=EXCHANGE,
                symbol=symbol,
                timeframe=TIMEFRAME,
                interval=window,
                status=CoverageStatus.FINAL,
                source=SOURCE,
                row_count=1,
            ),
            [bar],
        )
    stored = len(bars)
    read = sum(
        len(store.read(InstrumentId(symbol, EXCHANGE), TIMEFRAME, window))
        for symbol in sorted(bars)
    )
    files = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
    return {
        "data_dir": str(root),
        "appended_dialect": "udiff",
        "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "stored": stored,
        "read": read,
        "files": files,
    }


def run(data_dir: str | Path | None = None) -> dict[str, Any]:
    """Parse the three embedded fixtures, report each, and store the UDiFF result set.

    ``data_dir`` defaults to ``<repo>/output/02_import_nse_bhavcopy``; tests pass a
    temp directory, so nothing outside this repo is ever written.
    """
    root = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
    fixtures: list[dict[str, Any]] = []
    parsed: dict[str, dict[str, Bar]] = {}
    for dialect, csv_content in FIXTURES:
        bars = parse_bhavcopy_csv(csv_content, exchange=EXCHANGE)
        parsed[dialect] = bars
        fixtures.append(
            {
                "dialect": dialect,
                "rows_parsed": len(bars),
                "dropped_series": _dropped_series(csv_content, bars),
                "bars": _bar_rows(bars),
                "notes": NOTES[dialect],
            }
        )
    unrecognised = parse_bhavcopy_csv(UNRECOGNISED_CSV, exchange=EXCHANGE)
    return jsonable(
        {
            "fixtures": fixtures,
            "unrecognised_header": {"raises": False, "result": unrecognised},
            "store": _store_round_trip(root, parsed["udiff"]),
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Parquet store root (default: <repo>/output/02_import_nse_bhavcopy)",
    )
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON report here")
    args = parser.parse_args(argv)

    try:
        result = run(args.data_dir)
    except ValueError as exc:
        raise SystemExit(str(exc))

    text = json.dumps(result, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
