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