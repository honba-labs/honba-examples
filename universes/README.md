# 04 Universes – Learning Path

Walk these files in order.  Each step builds on the previous one and ends
with a production-ready catalog strategy.

| #  | File                                      | What you learn                                      |
|----|-------------------------------------------|-----------------------------------------------------|
| 01 | `01_nifty50_constituents.py`              | Resolve a static named universe                     |
| 02 | `02_banknifty_constituents.py`            | Sector / thematic universes                         |
| 03 | `03_alpha30_constituents.py`              | Nifty200 Alpha 30 seed + `load_alpha30()` helper    |
| 04 | `04_universe_rebalance.py`                | Generic equal-weight rebalancer (any universe)      |
| 05 | `05_custom_universe.py`                   | Build your own InstrumentId list                    |
| 06 | `06_alpha30_equal_weight_rebalance.py`    | Runnable end-to-end demo of the full strategy       |
| 07 | `07_alpha30_backtest.py`                  | In-sample training & out-of-sample backtest pipeline |

**Catalog destination**

```text
honba-strategies/alpha_universe/alpha30_equal_weight/
├── strategy.py      ← production version of 06
├── config.toml
└── README.md
```
Once the engine registers a real point-in-time Nifty200 Alpha 30 provider,
delete the seed fallback; every file above continues to work unchanged
because they all call `resolve_universe("nifty200_alpha_30")` first.

## 06 — Alpha-30 equal-weight rebalance

`06_alpha30_equal_weight_rebalance.py`

Equal-weight portfolio of the Nifty200 Alpha 30:

1. Load membership (live universe or seed list).
2. Allocate capital equally across all names.
3. Every 15 trading days:
   - sell stocks that left the index,
   - buy stocks that were added,
   - re-balance remaining holdings (trim winners, top up laggards).

Catalog counterpart: `honba-strategies/alpha_universe/alpha30_equal_weight`.