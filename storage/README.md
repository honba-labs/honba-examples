# Storage – persistence and research artifacts

These examples cover the persistence edge of a research workflow: how bars are laid out on
disk, how exchange EOD files and AMFI NAV publications are imported into the store, and how
a deterministic backtest is exported as a tearsheet. Everything runs offline and
deterministically — synthetic bars, embedded fixtures, no network and no wall clock — and
writes only under this repo's `output/` (or a `--data-dir` / `--out-dir` you pass).

**No trading strategy** is involved except in 04, which defines a tiny inline
`BuyFirstSellLast` class to produce a tearsheet.

## At a glance

| # | File | Strategy / focus | What you learn |
|---|---|---|---|
| 01 | `01_parquet_catalog.py` | — | `ParquetBarStore` on-disk layout and read/write contract; half-open windows; idempotent appends |
| 02 | `02_import_nse_bhavcopy.py` | — | Parse NSE EOD bhavcopy dialects into `Bar`s; series filtering, clamping, store round trip |
| 03 | `03_import_amfi_nav.py` | — | Import AMFI `NAVAll.txt` NAVs as flat bars; a skip vs. structural damage |
| 04 | `04_export_tearsheet.py` | `BuyFirstSellLast` (inline) | Deterministic backtest → `tearsheet.json` + `tearsheet.md` |

## Running them

```bash
python storage/01_parquet_catalog.py --data-dir /tmp/store --out catalog.json
python storage/02_import_nse_bhavcopy.py --data-dir /tmp/bhavcopy --out report.json
python storage/03_import_amfi_nav.py --data-dir /tmp/nav --out nav.json
python storage/04_export_tearsheet.py --out-dir /tmp/tearsheet --out report.json
pytest tests/unit -q -k "parquet_catalog or import_nse_bhavcopy or import_amfi_nav or export_tearsheet"
```

## The Parquet catalog

`ParquetBarStore` is where a research workflow keeps the bars it trusts: one append-only
Parquet file per instrument, timeframe and year at
`<data_dir>/catalog/<TIMEFRAME>/<EXCHANGE>/<SYMBOL>/<year>.parquet`, plus a JSON coverage
ledger. `append(coverage_record, bars)` is the only write path — it validates every bar,
merges by `ts` (re-appending is idempotent) and rewrites the ledger; `read(...)` takes a
**half-open UTC window** `[start 00:00, end 00:00)`, so pass `end + 1 day` to include a bar
stamped on `end`.

## The ingest edge

`honba.data.loaders.parse_bhavcopy_csv` and `honba_examples.amfi.parse_navall` are pure and
offline: the examples embed small fixtures instead of downloading, report what each dialect
produced, and surface malformed input as *data* in the report rather than repairing or
crashing. NAV rows become flat bars (`open == high == low == close == NAV`, zero volume)
filed under the `AMFI` exchange.

## The tearsheet

04 runs the inline `BuyFirstSellLast` strategy on synthetic weekday bars — it buys the first
test-window session and exits the session before the last, with next-open fills and T+2
settlement — then writes `tearsheet.json` (schema `honba-examples/tearsheet/v1` with a
`canonical_hash` over the payload) and a short human-readable `tearsheet.md`. Both files are
byte-identical between runs over the same inputs.
