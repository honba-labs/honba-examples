"""honba_examples.screening: Generic Point-in-Time Screener Engine.

Provides point-in-time screening primitives (fundamentals by period end plus a lag proxy) powered by DuckDB SQL:
- `screen_multibaggers`: Prospective "going-to-be" multibagger screener identifying early Stage 2
  breakouts, unexhausted consolidation bases, volume surges, and capital efficiency prior to --asof.
- `screen_value_and_dividend`: Contrarian value & high dividend yield screener near 52-week low.
- `screen_intelligent_investor`: Benjamin Graham's value screener (Defensive & Enterprising)
  enforcing Graham Number, Margin of Safety, financial condition, and valuation discipline.
- `screen_net_net`: Benjamin Graham's Net-Net / NCAV deep value screener.
- `screen_mutual_funds`: Mutual fund screener with underlying equity holdings lookthrough.
- `screen_swing_setups`: Swing-trading "A+ setup" scan (breakout/pullback, relative strength,
  volatility and liquidity gates) over a top-down universe such as Nifty50.
- Point-in-time price, mutual funds, and DuckDB fundamentals loaders.
"""

from __future__ import annotations

from honba_examples.screening.graham import (
    graham_thresholds,
    screen_intelligent_investor,
    screen_net_net,
)
from honba_examples.screening.momentum import screen_multibaggers
from honba_examples.screening.mutual_funds import screen_mutual_funds
from honba_examples.screening.paths import (
    find_catalog_path,
    find_duckdb_path,
    find_mutual_funds_db_path,
    get_latest_market_date,
)
from honba_examples.screening.swing import screen_swing_setups
from honba_examples.screening.value import screen_value_and_dividend

__all__ = [
    "find_catalog_path",
    "find_duckdb_path",
    "find_mutual_funds_db_path",
    "get_latest_market_date",
    "graham_thresholds",
    "screen_intelligent_investor",
    "screen_multibaggers",
    "screen_mutual_funds",
    "screen_net_net",
    "screen_swing_setups",
    "screen_value_and_dividend",
]
