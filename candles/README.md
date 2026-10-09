# Candles – bar construction

Bars arrive from two directions: an adapter serves them historically, or you build them
yourself from a tick stream. Neither is a network call by default — every example here runs
offline and deterministically on the `fake` adapter (synthetic one-minute bars and quote
ticks), and **none of them runs a trading strategy**: the lesson is how bars are fetched,
folded and aligned.

## At a glance

| #  | File                       | Strategy / focus | What you learn |
|----|----------------------------|------------------|----------------|
| 01 | `01_historical_candles.py` | — | `historical_bars` over a half-open `[start, end)` of tz-aware datetimes; `timeframe` is a canonical name |
| 02 | `02_realtime_candles.py`   | — | Turn pushed `QuoteTick`s into OHLC frames; release the subscription in `finally` |
| 03 | `03_custom_aggregation.py` | — | The OHLC fold: open from the first bar, extremes, close from the last, volume summed |
| 04 | `04_multi_timeframe.py`    | — | One fetch, many resolutions, and the alignment invariants between them |

## Where the aggregation lives

Honba ships no resampler, so the rule is in `honba_examples/bars.py`, which is
unit-tested on its own (`tests/unit/test_bars.py`):

* buckets are **UTC** and right-open: `[start, start + timeframe)`;
* the aggregated `ts` is the **first member's** timestamp, so a daily bar folded
  from NSE intraday keeps the 09:15 IST stamp the Parquet store uses;
* `1w` buckets start on Monday.

```python
from honba_examples.bars import aggregate, bucket_start, timeframe_seconds
weekly = aggregate(minute_bars, "1w")
```

## Running them

```bash
python candles/01_historical_candles.py --timeframe 1m --instrument RELIANCE \
    --start 2025-06-02T09:15:00+00:00 --end 2025-06-02T09:20:00+00:00
python candles/02_realtime_candles.py --advances 5
python candles/03_custom_aggregation.py --timeframe 5m --out bars.json
python candles/04_multi_timeframe.py --out multi_timeframe.json
pytest tests/unit -q -k "historical_candles or realtime_candles or custom_aggregation or multi_timeframe"
```

## The fake adapter is a test double, not a feed

`FakeAdapter.historical_bars` validates the timeframe but always answers with the
same one-minute series (five bars by default, `bars_per_request` to widen it), so
03 and 04 always request `1m` and resample locally. Real adapters return the
requested timeframe; the fold and the alignment checks stay the same.
