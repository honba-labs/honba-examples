"""03_import_amfi_nav: import AMFI's daily NAVAll.txt publication into the Parquet store.

NAVAll.txt is the sheet AMFI publishes every working day: one UTF-8 file per publication
date, semicolon-separated, an ``Applic Date:`` header, and four fields per line - scheme
code, scheme name, NAV, date. The import here is fully offline: two embedded publications
stand in for the download, one scheme arrives with a missing NAV (``-``) and a third file
arrives with a structurally damaged line. The two failures are not the same: a missing value
is skipped and reported with its line, code and reason, while structural damage is refused
by ``honba_examples.amfi.parse_navall`` and reported as data rather than repaired.

Parsed rows become NAV bars (``open == high == low == close == NAV``, volume zero), are
appended per scheme under the ``AMFI`` exchange - the publisher is the venue for a series
that is not traded - and are read back over the union of the publication dates, one half-open
window that ends the day after the last NAV.

Run::

    python storage/03_import_amfi_nav.py
    python storage/03_import_amfi_nav.py --data-dir /tmp/nav --out nav.json

Pitfalls::

    - NAV timing: a NAV is published after the close and is stamped here at 09:15 IST of its own
      date. Using nav[D] to decide on D is lookahead. Each row keeps its own date, so a late-NAV
      scheme can carry an older date than the file's Applic Date.
    - Dedupe is overwrite: the store merges by ts, so a repeated or restated NAV for the same scheme
      and date replaces the earlier one, and a stale NAV repeated in the next day's file is not
      flagged. The ledger row_count adds both appends.
    - Skips become gaps: a row with a missing NAV (`-`) is dropped and reported, but the scheme's
      ledger record still spans first to last date as FINAL. Holidays and skipped days inside it are
      not marked, so a later reader assumes continuity that is not there.
    - Identity and survivorship: the AMFI code is the plan and option (Direct, Regular, Growth,
      IDCW). The daily sheet lists only live schemes, so merged and closed ones vanish from the
      feed; keep what you imported. A structurally damaged line is refused, not repaired, and the
      rest of that file is not imported.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any

from honba.adapters.errors import AdapterNotFound
from honba.domain.instrument import InstrumentId
from honba.screener.coverage import CoverageRecord, CoverageStatus, DateInterval
from honba.screener.store import ParquetBarStore

from honba_examples.amfi import EXCHANGE, NavRow, NavSkip, nav_bars, parse_navall
from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.jsonable import jsonable

NAVALL_FIRST_TXT = """Applic Date: 02-Jun-2025

100001;Sample Flexi Cap Fund - Growth;123.4567;02-Jun-2025
100002;Sample Index Fund - Direct Plan;89.1011;02-Jun-2025
100003;Sample Debt Fund - Growth;-;02-Jun-2025
"""

NAVALL_SECOND_TXT = """Applic Date: 03-Jun-2025

100001;Sample Flexi Cap Fund - Growth;124.1000;03-Jun-2025
100002;Sample Index Fund - Direct Plan;88.7500;03-Jun-2025
100003;Sample Debt Fund - Growth;51.2000;03-Jun-2025
"""

MALFORMED_NAVALL_TXT = """Applic Date: 04-Jun-2025

100001;Sample Flexi Cap Fund - Growth;124.5000;04-Jun-2025
100002;Sample Index Fund - Direct Plan;88.9000
"""
"""A publication whose second line lost its date field: three fields, never four."""

_SOURCES = (
    ("NAVALL_FIRST_TXT", NAVALL_FIRST_TXT),
    ("NAVALL_SECOND_TXT", NAVALL_SECOND_TXT),
)
_TIMEFRAME = "1D"
_OHLC_NOTE = (
    "NAV is one price for the whole day, so a NAV bar has no OHLC: "
    "open = high = low = close = NAV and volume = 0"
)
_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "output" / "03_import_amfi_nav"


def _rows_by_code(rows: list[NavRow]) -> dict[str, list[NavRow]]:
    grouped: dict[str, list[NavRow]] = {}
    for row in rows:
        grouped.setdefault(row.code, []).append(row)
    return dict(sorted(grouped.items()))


def _union_window(rows: list[NavRow]) -> DateInterval:
    """Publication dates as one half-open window: the day after the last NAV is the end."""
    dates = [row.date for row in rows]
    return DateInterval(min(dates), max(dates) + dt.timedelta(days=1))


def _record(code: str, code_rows: list[NavRow]) -> CoverageRecord:
    return CoverageRecord(
        exchange=EXCHANGE,
        symbol=code,
        timeframe=_TIMEFRAME,
        interval=DateInterval(code_rows[0].date, code_rows[-1].date + dt.timedelta(days=1)),
        status=CoverageStatus.FINAL,
        source="NAVAll.txt",
        row_count=len(code_rows),
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


def _utc_date(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts / 1e9, tz=dt.timezone.utc).date().isoformat()


def _read_back(
    store: ParquetBarStore, codes: list[str], window: DateInterval
) -> list[dict[str, Any]]:
    entries = []
    for code in codes:
        bars = store.read(InstrumentId(code, EXCHANGE), _TIMEFRAME, window)
        entries.append(
            {
                "code": code,
                "count": len(bars),
                "dates": [_utc_date(bar.ts) for bar in bars],
                "flat": all(bar.open == bar.high == bar.low == bar.close for bar in bars),
            }
        )
    return entries


def _scheme_series(code: str, code_rows: list[NavRow]) -> dict[str, Any]:
    first, last = code_rows[0].nav, code_rows[-1].nav
    return {
        "code": code,
        "name": code_rows[0].name,
        "dates": [row.date.isoformat() for row in code_rows],
        "navs": [row.nav for row in code_rows],
        "first": first,
        "last": last,
        "change_pct": round((last - first) / first * 100, 4),
    }


def run(data_dir: Path | None = None) -> dict[str, Any]:
    """Parse the embedded publications, append their NAV bars and read them back.

    The report carries the import ledger (``sources``, ``rows_parsed``, ``rows_skipped``,
    ``structural_damage``), the store it wrote (``window``, ``files``, ``read_back``) and the
    series it reconstructs per scheme (``schemes``, ``ohlc_note``). Every failure that can
    happen offline is data in the report; nothing here downloads, and nothing is repaired.
    """
    store = ParquetBarStore(Path(data_dir) if data_dir is not None else _DEFAULT_DATA_DIR)

    rows: list[NavRow] = []
    sources: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for name, text in _SOURCES:
        file_skipped: list[NavSkip] = []
        parsed = parse_navall(text, skipped=file_skipped)
        sources.append({"source": name, "rows": len(parsed), "skipped": len(file_skipped)})
        rows.extend(parsed)
        skipped.extend(
            {
                "source": name,
                "line": skip.line,
                "code": skip.code,
                "reason": skip.reason,
            }
            for skip in file_skipped
        )

    try:
        parse_navall(MALFORMED_NAVALL_TXT)
    except ValueError as exc:
        damage = str(exc)
    else:
        damage = None

    by_code = _rows_by_code(rows)
    window = _union_window(rows)
    for code, code_rows in by_code.items():
        store.append(_record(code, code_rows), nav_bars(code_rows))

    report = {
        "data_dir": str(store.data_dir),
        "sources": sources,
        "rows_parsed": len(rows),
        "rows_skipped": skipped,
        "structural_damage": {"source": "MALFORMED_NAVALL_TXT", "error": damage},
        "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "files": _files(store.data_dir),
        "read_back": _read_back(store, list(by_code), window),
        "schemes": [_scheme_series(code, code_rows) for code, code_rows in by_code.items()],
        "ohlc_note": _OHLC_NOTE,
    }
    return jsonable(report)


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="store root (default: <repo>/output/03_import_amfi_nav)",
    )
    add_out_arg(parser)
    args = parser.parse_args(argv)

    try:
        result = run(args.data_dir)
    except (ValueError, AdapterNotFound) as exc:
        raise SystemExit(str(exc))

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
