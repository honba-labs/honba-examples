"""Swing-trading "A+ setup" screener (startrader.com-style breakout/pullback scan)."""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Any

from honba_examples.screening.common import DEFAULT_MAX_STALE_DAYS, stale_price_sql

logger = logging.getLogger(__name__)

__all__ = ["screen_swing_setups"]

#: "A+" > "A" > "B" > "C"; used to filter scored candidates by ``min_grade``.
_GRADE_RANK = {"A+": 4, "A": 3, "B": 2, "C": 1}


def _universe_symbols(universe: str) -> list[str] | None:
    """Symbols to restrict the scan to, or ``None`` for "every symbol dir in the catalog"."""
    if universe == "all":
        return None
    if universe == "nifty50":
        from honba.markets.india.universes import resolve_universe

        return [instrument.symbol for instrument in resolve_universe("nifty50")]
    raise ValueError(f"unknown --universe {universe!r}; expected 'nifty50' or 'all'")


def screen_swing_setups(
    catalog_dir: Path,
    asof_date: dt.date,
    universe: str = "nifty50",
    lookback_days: int = 260,
    min_adtv: float = 5_000_000.0,
    min_price: float = 50.0,
    max_price: float = 0.0,
    min_atr_pct: float = 1.0,
    max_atr_pct: float = 5.0,
    min_rs_excess: float = 0.0,
    min_vol_surge: float = 1.5,
    pullback_pct: float = 3.0,
    max_gap_down_pct: float = 7.0,
    max_stale_days: int | None = DEFAULT_MAX_STALE_DAYS,
    min_grade: str = "A+",
    top: int | None = 10,
) -> list[dict[str, Any]]:
    """Screen for a swing-trading "A+ setup" strictly as of ``asof_date``.

    Distilled from startrader.com's "How to Select Stocks for Swing Trading in India": scan a
    top-down universe (default Nifty50, ``--universe nifty50``; ``all`` scans every symbol
    directory in the catalog), require a liquid, rising, moderate-volatility name that is either
    breaking out to a new 20-day high on a volume surge, or pulling back to a rising 20/50-day
    moving average, and reject gap-downs. Gates (a symbol must pass ALL):

    1. Staleness: last bar within ``max_stale_days`` of ``asof_date`` (delisted/suspended guard).
    2. Liquidity: 20-day average daily traded value (``close * volume``) >= ``min_adtv``.
    3. Price band: ``min_price <= close`` and, if ``max_price > 0``, ``close <= max_price``.
    4. Volatility: ``atr_pct`` (simple 14-day ATR / close) within ``[min_atr_pct, max_atr_pct]``.
    5. Trend: ``close > sma50`` and ``sma50`` is rising (>= its value ~20 bars ago).
    6. Relative strength: 20-day return beats the scanned universe's own as-of average 20-day
       return (an equal-weight benchmark proxy; the catalog holds no index/benchmark series) by
       more than ``min_rs_excess``.
    7. Gap-down: today's open did not gap down more than ``max_gap_down_pct`` below the prior close.
    8. Pattern: breakout (new 20-day high on >= ``min_vol_surge`` volume) OR pullback (within
       ``pullback_pct`` above a rising 20-day SMA, not yet broken below it).

    Every symbol that clears all 8 gates is scored (0-100, hand-weighted: relative strength up to
    30, volume surge up to 20, moving-average alignment 5 or 15, an ATR "sweet spot" bonus up to
    15, a pattern bonus of 12 or 20) and graded (A+ >= 80, A >= 65, B >= 50, else C). Candidates
    are THEN filtered to ``grade >= min_grade`` (default "A+") and sorted by score, with ``top``
    applied last -- the scoring/grading happens for every gate-passing symbol regardless of
    ``min_grade``, matching the gate-then-rank-then-filter order the other screeners use.

    A symbol whose last bar is more than ``max_stale_days`` calendar days before ``asof_date`` is
    skipped entirely (see ``stale_price_sql``); ``None`` or ``0`` disables that check.
    """
    import duckdb

    symbols = _universe_symbols(universe)
    if symbols is not None and not symbols:
        return []

    catalog_glob = str(catalog_dir / "*" / "*.parquet")
    lookback_start = (asof_date - dt.timedelta(days=lookback_days)).isoformat()
    asof_str = asof_date.isoformat()
    universe_filter = (
        "AND symbol IN (" + ", ".join(f"'{s}'" for s in symbols) + ")"
        if symbols is not None
        else ""
    )
    max_price_filter = f"AND close <= {max_price}" if max_price and max_price > 0 else ""

    con = duckdb.connect()

    query = f"""
    WITH bars AS (
        SELECT
            regexp_extract(filename, 'NSE/([^/]+)/', 1) as symbol,
            epoch_ms(ts // 1000000)::DATE as bar_date,
            open, high, low, close, volume
        FROM read_parquet('{catalog_glob}', filename=true)
        WHERE bar_date >= '{lookback_start}' AND bar_date <= '{asof_str}'
          {universe_filter}
    ),
    win AS (
        SELECT
            symbol, bar_date, open, high, low, close, volume,
            LAG(close) OVER (PARTITION BY symbol ORDER BY bar_date) as prev_close,
            AVG(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as sma20,
            AVG(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) as sma50,
            AVG(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 69 PRECEDING AND 20 PRECEDING) as sma50_prior,
            AVG(close) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 29 PRECEDING AND 10 PRECEDING) as sma20_prior,
            MAX(high) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) as high20_prior,
            AVG(volume) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) as vol20_baseline,
            AVG(close * volume) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as adtv20,
            MIN(low) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) as recent_low10,
            NTH_VALUE(close, 21) OVER (
                PARTITION BY symbol ORDER BY bar_date DESC ROWS BETWEEN CURRENT ROW AND 20 FOLLOWING
            ) as close_20ago,
            ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY bar_date DESC) as rn
        FROM bars
    ),
    tr_calc AS (
        SELECT *,
            GREATEST(high - low, ABS(high - prev_close), ABS(low - prev_close)) as tr
        FROM win
    ),
    atr AS (
        SELECT *,
            AVG(tr) OVER (PARTITION BY symbol ORDER BY bar_date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) as atr14
        FROM tr_calc
    ),
    asof_rows AS (
        SELECT * FROM atr WHERE rn = 1
    ),
    scored AS (
        SELECT
            *,
            atr14 / close * 100 as atr_pct,
            (close / close_20ago - 1.0) * 100 as ret_20d,
            GREATEST(0.0, (prev_close - open) / NULLIF(prev_close, 0) * 100) as gap_down_pct,
            volume / NULLIF(vol20_baseline, 0) as vol_surge,
            (close >= high20_prior AND volume / NULLIF(vol20_baseline, 0) >= {min_vol_surge}) as breakout_flag,
            (
                close <= sma20 * (1 + {pullback_pct} / 100.0)
                AND close >= sma20 * 0.98
                AND sma20 >= sma20_prior
            ) as pullback_flag
        FROM asof_rows
    ),
    benched AS (
        SELECT
            *,
            ((close / close_20ago - 1.0) * 100) - AVG((close / close_20ago - 1.0) * 100) OVER () as rs_excess
        FROM scored
    ),
    gated AS (
        SELECT *
        FROM benched
        WHERE 1=1
          {stale_price_sql(asof_date, max_stale_days, "bar_date")}
          AND adtv20 >= {min_adtv}
          AND close >= {min_price}
          {max_price_filter}
          AND atr_pct BETWEEN {min_atr_pct} AND {max_atr_pct}
          AND close > sma50 AND sma50 >= sma50_prior
          AND rs_excess > {min_rs_excess}
          AND gap_down_pct <= {max_gap_down_pct}
          AND (breakout_flag OR pullback_flag)
    )
    SELECT
        symbol,
        round(close, 2) as close,
        CASE WHEN breakout_flag AND pullback_flag THEN 'breakout+pullback'
             WHEN breakout_flag THEN 'breakout'
             WHEN pullback_flag THEN 'pullback'
        END as setup,
        round(atr_pct, 2) as atr_pct,
        round(ret_20d, 2) as ret_20d,
        round(rs_excess, 2) as rs_excess,
        round(vol_surge, 2) as vol_surge,
        round(sma20, 2) as sma20,
        round(sma50, 2) as sma50,
        round(GREATEST(recent_low10, sma50 * 0.97), 2) as stop_loss,
        round(gap_down_pct, 2) as gap_down_pct,
        round(
            LEAST(
                LEAST(GREATEST(rs_excess, 0.0), 20.0) * 1.5
                + COALESCE(LEAST(vol_surge, 3.0), 0.0) * (20.0 / 3.0)
                + CASE WHEN close > sma20 THEN 15.0 ELSE 5.0 END
                + LEAST(GREATEST(COALESCE(
                    CASE
                        WHEN atr_pct BETWEEN 2.0 AND 3.5 THEN 15.0
                        WHEN atr_pct < 2.0 THEN
                            15.0 * (atr_pct - {min_atr_pct}) / NULLIF(2.0 - {min_atr_pct}, 0.0)
                        ELSE
                            15.0 * ({max_atr_pct} - atr_pct) / NULLIF({max_atr_pct} - 3.5, 0.0)
                    END,
                    0.0), 0.0), 15.0)
                + CASE
                    WHEN breakout_flag AND pullback_flag THEN 20.0
                    WHEN breakout_flag THEN 20.0
                    ELSE 12.0
                  END,
                100.0
            ),
            1
        ) as score
    FROM gated
    ORDER BY score DESC, rs_excess DESC
    """

    rows = con.execute(query).fetchall()
    columns = [d[0] for d in con.description]
    con.close()

    grade_floor = _GRADE_RANK.get(min_grade)
    if grade_floor is None:
        raise ValueError(
            f"unknown --min-grade {min_grade!r}; expected one of {sorted(_GRADE_RANK)}"
        )

    candidates: list[dict[str, Any]] = []
    for r in rows:
        row = dict(zip(columns, r, strict=True))
        score = float(row["score"])
        grade = "A+" if score >= 80 else "A" if score >= 65 else "B" if score >= 50 else "C"
        if _GRADE_RANK[grade] < grade_floor:
            continue
        close = float(row["close"])
        stop_loss = float(row["stop_loss"])
        risk = close - stop_loss
        candidates.append(
            {
                "symbol": row["symbol"],
                "asof_date": asof_str,
                "close": close,
                "grade": grade,
                "score": score,
                "setup": row["setup"],
                "atr_pct": float(row["atr_pct"]) if row["atr_pct"] is not None else None,
                "ret_20d": float(row["ret_20d"]) if row["ret_20d"] is not None else None,
                "rs_excess": float(row["rs_excess"]) if row["rs_excess"] is not None else None,
                "vol_surge": float(row["vol_surge"]) if row["vol_surge"] is not None else None,
                "gap_down_pct": float(row["gap_down_pct"])
                if row["gap_down_pct"] is not None
                else None,
                "sma20": float(row["sma20"]),
                "sma50": float(row["sma50"]),
                "stop_loss": stop_loss,
                "target_1r": round(close + risk, 2),
                "target_2r": round(close + 2 * risk, 2),
            }
        )

    if top and top > 0:
        candidates = candidates[: int(top)]
    return candidates
