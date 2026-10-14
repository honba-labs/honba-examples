"""honba_examples.screening.dispatch: strategy-name -> screener routing and candidate stubs.

Maps the ``--strategy`` CLI choice onto the right point-in-time screener, and builds the minimal
candidate record used when symbols arrive from ``--symbols`` or a stdin pipe instead.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from honba_examples.screening.common import DEFAULT_MAX_STALE_DAYS
from honba_examples.screening.graham import screen_intelligent_investor, screen_net_net
from honba_examples.screening.momentum import screen_multibaggers
from honba_examples.screening.value import screen_value_and_dividend

__all__ = ["run_screener", "symbol_stub"]


def run_screener(
    strategy: str,
    catalog_dir: Path,
    asof_date: dt.date,
    train_years: int,
    top: int,
    graham_mode: str = "defensive",
    max_stale_days: int | None = DEFAULT_MAX_STALE_DAYS,
    allow_missing: bool = False,
) -> list[dict[str, Any]]:
    """Run the screener named by ``strategy`` as of ``asof_date`` (zero lookahead)."""
    if strategy == "multibagger":
        return screen_multibaggers(
            catalog_dir=catalog_dir,
            asof_date=asof_date,
            train_years=train_years,
            max_stale_days=max_stale_days,
            allow_missing=allow_missing,
            top=top,
        )
    if strategy in ("graham", "intelligent_investor"):
        return screen_intelligent_investor(
            catalog_dir=catalog_dir,
            asof_date=asof_date,
            train_years=train_years,
            mode=graham_mode,
            max_stale_days=max_stale_days,
            allow_missing=allow_missing,
            top=top,
        )
    if strategy in ("net_net", "ncav"):
        return screen_net_net(
            catalog_dir=catalog_dir,
            asof_date=asof_date,
            train_years=train_years,
            max_stale_days=max_stale_days,
            allow_missing=allow_missing,
            top=top,
        )
    return screen_value_and_dividend(
        catalog_dir=catalog_dir,
        asof_date=asof_date,
        train_years=train_years,
        mode=strategy,
        max_stale_days=max_stale_days,
        allow_missing=allow_missing,
        top=top,
    )


def symbol_stub(symbol: str, asof_date: dt.date) -> dict[str, Any]:
    """Minimal candidate record for explicitly-provided or piped symbols."""
    return {
        "symbol": symbol,
        "company_name": symbol,
        "asof_date": asof_date.isoformat(),
        "close": 0.0,
        "ret_1y": 0.0,
        "ret_3m": 0.0,
        "train_ret_pct": 0.0,
        "pct_from_high": 0.0,
        "pct_from_low": 0.0,
        "low52": 0.0,
        "sma200": 0.0,
        "sma200_rising": True,
        "vol_surge": 1.0,
        "roce_pct": None,
        "debt_to_equity": None,
        "mb_score": 100.0,
        "pe_ratio": None,
        "pb_ratio": None,
        "graham_product": None,
        "graham_num": 0.0,
        "margin_of_safety_pct": 0.0,
        "current_ratio": None,
        "div_yield_pct": 0.0,
    }
