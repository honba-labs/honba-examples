"""The one shared Rich style palette for every example's table output.

Every script that builds a :class:`honba.display.Column` list should reach for these
constants/helpers instead of choosing a Rich style string ad hoc. That is the actual fix for
tables looking inconsistent across scripts: the same semantic column (a symbol, a composite
score, a signed return) gets the same color everywhere, whether the table is built by a
standalone screener, the chained screener+SIP flow, or a universe listing.

Palette:

- Symbol / primary identifier (ticker, scheme code): ``SYMBOL`` (bold cyan).
- Secondary/contextual columns (company name, category, reference-only prices like a 52-week
  low or a stop-loss, dates): ``SECONDARY`` (dim).
- Composite score that ranks candidates against each other (mb_score, swing score, mutual-fund
  score): ``SCORE`` (bold yellow).
- Volume/liquidity surge ratios: ``SURGE`` (plain yellow) -- kept distinct from ``SCORE`` so
  yellow does not mean two different things in the same table.
- Price columns in general: no entry here on purpose -- leave ``style=None`` (the table's
  default) unless the price is secondary/reference, in which case use ``SECONDARY``.
- Any signed, sign-meaningful quantity (profit, margin of safety, RS-excess, a return that can
  go negative): :func:`pnl_style_fn` -- green when >= 0, red when < 0. Pass it as
  ``Column(..., style_fn=pnl_style_fn)``, not a static ``style``.
- Swing-screener style letter grade bands (A+/A/B/C): :func:`grade_style_fn`.
"""

from __future__ import annotations

from typing import Any

__all__ = ["SCORE", "SECONDARY", "SURGE", "SYMBOL", "grade_style_fn", "pnl_style_fn"]

SYMBOL = "bold cyan"
SECONDARY = "dim"
SCORE = "bold yellow"
SURGE = "yellow"


def pnl_style_fn(value: Any) -> str | None:
    """Green when ``value >= 0``, red when ``value < 0``; ``None`` (no override) otherwise."""
    try:
        return "green" if float(value) >= 0 else "red"
    except (TypeError, ValueError):
        return None


_GRADE_STYLES = {"A+": "bold green", "A": "green", "B": "yellow"}


def grade_style_fn(value: Any) -> str | None:
    """Swing-screener letter grade: A+/A -> green shades, B -> yellow, anything else -> dim."""
    return _GRADE_STYLES.get(str(value).strip().upper(), "dim")
