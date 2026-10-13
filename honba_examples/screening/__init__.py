"""honba_examples.screening: Generic Point-in-Time Screener Engine.

Provides zero-lookahead screening primitives powered by DuckDB SQL:
- `screen_multibaggers`: Prospective "going-to-be" multibagger screener identifying early Stage 2
  breakouts, unexhausted consolidation bases, volume surges, and capital efficiency prior to --asof.
- `screen_value_and_dividend`: Contrarian value & high dividend yield screener near 52-week low.
- `screen_intelligent_investor`: Benjamin Graham's value screener (Defensive & Enterprising)
  enforcing Graham Number, Margin of Safety, financial condition, and valuation discipline.
- Point-in-time price and DuckDB fundamentals loaders.

Submodules: :mod:`paths` (catalog/DuckDB discovery), :mod:`momentum`, :mod:`value` and
:mod:`graham` (screener rules). Public names are re-exported here.
"""

from __future__ import annotations

from honba_examples.screening.graham import screen_intelligent_investor, screen_net_net
from honba_examples.screening.momentum import screen_multibaggers
from honba_examples.screening.paths import (
    find_catalog_path,
    find_duckdb_path,
    get_latest_market_date,
)
from honba_examples.screening.value import screen_value_and_dividend

__all__ = [
    "find_catalog_path",
    "find_duckdb_path",
    "get_latest_market_date",
    "screen_intelligent_investor",
    "screen_multibaggers",
    "screen_net_net",
    "screen_value_and_dividend",
]
