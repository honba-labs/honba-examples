# Universes – Learning Path

Walk these files in order. Each step builds on the previous one and ends
with a production-ready catalog strategy.

| #  | File                                          | What you learn                                                  |
|----|-----------------------------------------------|-----------------------------------------------------------------|
| 01 | `01_nifty50_constituents.py`                  | Resolve a static named universe; print an InstrumentId table   |
| 02 | `02_banknifty_constituents.py`                | Sector / thematic universes — same API, different name          |
| 03 | `alpha30_constituents.py`                     | Nifty200 Alpha 30 seed + `load_alpha30()` helper                |
| 04 | `04_alpha30_generic_rebalancer.py`            | Generic equal-weight rebalancer (any universe, any exchange)       |
| 05 | `05_alpha30_custom_universe.py`               | Build and register your own InstrumentId list as a universe     |
| 06 | `06_alpha30_equal_weight_demo.py`             | Runnable end-to-end demo of the Alpha 30 equal-weight strategy  |
| 07 | `07_alpha30_backtest.py`                      | Honba-native backtest: next-open fills, T+2, paise, run.json    |
| 08 | `08_alpha30_union_ewr_backtest.py`            | Jesse-parity port of the same rebalance, for engine comparison  |

## 01 – 03  Core concepts

| File | Key idea |
|------|----------|
| 01   | `resolve_universe("nifty50", exchange="NSE")` returns `list[InstrumentId]` |
| 02   | Sector universes use the same call — just swap the name |
| 03   | When a universe isn't registered yet, fall back to a seed list via `load_alpha30()` |

## 04 – 07  Alpha 30 equal-weight strategy progression

These four files are a self-contained learning arc that ends at a
runnable backtest:

```
04  generic rebalancer class   (any universe, 3-action rebalance logic)
        ↓
05  custom universe             (register your own ticker list)
        ↓
06  Alpha 30 demo               (wires 03 + 04 into a runnable strategy)
        ↓
07  backtest pipeline           (Honba-native reference backtest)
```

### 04 — Generic equal-weight rebalancer

`04_alpha30_generic_rebalancer.py`

Three mandatory actions on every rebalance day:

1. **Exit** — sell stocks that left the index.
2. **Enter** — buy stocks that were added.
3. **Re-balance** — trim over-weight survivors, top-up under-weight ones.

Pass `universe="custom_infra_basket"` (or any registered name) to reuse
this class on a completely different basket.

### 05 — Custom universe

`05_alpha30_custom_universe.py`

Shows how to build a hand-crafted basket (infrastructure names used as
an example), register it with `UNIVERSES`, and verify the round-trip
through `resolve_universe()`.  Plug `CUSTOM_UNIVERSE_NAME` straight into
`UniverseEqualWeightRebalance`.

### 06 — Alpha 30 equal-weight demo

`06_alpha30_equal_weight_demo.py`

Runnable end-to-end demo — equal-weight Nifty200 Alpha 30, rebalanced
every 15 trading days.  Membership is always re-fetched from the engine
API so index joiners/leavers are applied automatically.

### 07 — Backtest pipeline

`07_alpha30_backtest.py` is the Honba-native reference backtest of the
`alpha30_equal_weight` catalog strategy:

- Strategy loaded from `honba-strategies` by registry name
  (`--strategies-dir`, `$HONBA_STRATEGIES_DIR`, or the sibling checkout).
- Universe resolved once from the universe the strategy trades; members with
  no bars in the test window are reported (`--require-full-coverage` fails).
- Orders fill at the **next session's open**, sells before buys, NSE delivery
  costs per leg in paise, and the engine's settlement cycle (T+2 on NSE): buys
  wait for sale proceeds and are cut to the cash available.
- `--warmup-days` bars feed indicators only; warm-up cannot trade, and only
  test-window fills, fees and turnover are counted. Alpha-30 has no indicators,
  so its default warm-up is 0.
- Writes `--out-dir/run.json`: config, universe, data coverage, metrics, fills,
  order events, equity curve (integer paise) and input/result/run hashes.

```bash
python universes/07_alpha30_backtest.py --test-start 2026-06-01 --test-end 2026-09-20 \
    --out-dir /tmp/alpha30
```

`--test-end` defaults to the latest bar in the store, resolved at run time and
recorded as `test_end_source` in run.json.

### 08 — Jesse parity

`08_alpha30_union_ewr_backtest.py` ports Jesse's portfolio rebalance (fills at
the session close, flat fee) so the two engines can be diffed session by
session. Use 07 for Honba results; 08 only for the comparison
(`--jesse-trades PATH` points at Jesse's `trades.csv`).
