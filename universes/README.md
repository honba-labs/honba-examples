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
| 07 | `07_alpha30_backtest.py`                      | In-sample training & out-of-sample backtest pipeline            |

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
07  backtest pipeline           (in-sample training + OOS evaluation)
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

`07_alpha30_backtest.py`

Full evaluation:

- **In-sample training** : 2022-01-01 → 2025-12-30
- **Out-of-sample test** : 2026-01-01 → today

Reports CAGR, Sharpe, max drawdown, turnover, and total fees (NSE
delivery cost model).

**Catalog destination**

```text
honba-strategies/alpha_universe/alpha30_equal_weight/
├── strategy.py      ← production version of 06
├── config.toml
└── README.md
```

Once the engine registers a real point-in-time Nifty200 Alpha 30 provider,
delete the seed fallback in 03; every file above continues to work unchanged
because they all call `resolve_universe("nifty200_alpha_30")` first.