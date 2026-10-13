"""ai_research/03_nl_screener: End-to-end Natural Language Stock Screener & Historical Backtester.

Translates free-text trader queries into validated filter expressions and executable
Python predicates, then executes them across the local stock universe (Parquet bars & fundamentals).

Supports:
1. Latest Market Screening: Evaluates live/latest bar snapshot.
2. Point-in-Time Historical Screening (`--asof` or "5 years ago"): Slices bar history to that
   exact date without lookahead bias.
3. Forward Backtesting & Multibagger Tracking: For historical screens, tracks the forward
   performance of every matched stock up to today, computing Total Return %, CAGR %, and
   Bag Multipliers (2x, 5x, 10x).

Run::

    # Point-in-time screen 5 years ago with forward multibagger backtest
    python ai_research/03_nl_screener.py --asof 2021-10-11 --query "market_cap > 25000Cr and close above 50-day moving average"

    # Natural language historical backtest query
    python ai_research/03_nl_screener.py --query "close above 50-day moving average and volume > 500k 5 years ago"

    # Latest live screen
    python ai_research/03_nl_screener.py --query "volume > 500k and rsi < 40"
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba.ai.llm.provider import LlmPort
from honba.cli.data import _STORE
from honba.data.query.quantity import parse_quantity
from honba.entities.instrument import InstrumentId
from honba.markets.india.universes import resolve_universe
from honba.screener.coverage import DateInterval

from honba_examples.cli import make_parser
from honba_examples.screening.cli_args import add_nl_screener_args
from honba_examples.screening.nl_render import render_nl_result
from honba_examples.screening.nl_support import (
    MCAP_UNIT_MULTIPLIER,
    NIFTY50_SHARES_CR,
    VOLUME_UNIT_MULTIPLIER,
    OllamaLlm,
    basket_statistics,
    build_point_in_time_row,
    forward_performance,
    resolve_asof,
    safe_eval_predicate,
)
from honba_examples.screening.nl_support import resolve_llm_provider as _resolve_llm_provider

__all__ = [
    "NIFTY50_SHARES_CR",
    "OllamaLlm",
    "RuleBasedLlamaTranslator",
    "generate_python_predicate",
    "main",
    "resolve_llm_provider",
    "run_nl_screener",
    "safe_eval_predicate",
]

# ---------------------------------------------------------------------------
# Tier 1: Natural Language -> filter expression (the lesson)
# ---------------------------------------------------------------------------
# An LLM (Ollama) turns free text into a small filter DSL such as
# "market_cap > 250000000000 AND close > sma50". With no LLM available we fall back to the
# deterministic rule-based translator below, which implements the same contract with regexes.
# Plumbing (Ollama client, backend probing) lives in honba_examples.screening.nl_support.


class RuleBasedLlamaTranslator(LlmPort):
    """Deterministic offline translator mimicking Llama grammar translation for standard queries."""

    def complete(
        self,
        messages: Sequence[dict[str, str]],
        grammar: str | None = None,
        schema: dict[str, Any] | None = None,
        seed: int | None = None,
        temperature: float = 0.0,
    ) -> str:
        user_msg = messages[-1]["content"].lower()

        # Clean out temporal words from condition parser
        cleaned_msg = re.sub(r"\b\d+\s*years?\s*ago\b", "", user_msg)
        cleaned_msg = re.sub(r"\bas of \d{4}-\d{2}-\d{2}\b", "", cleaned_msg)
        cleaned_msg = re.sub(r"\bin 201\d|in 202\d\b", "", cleaned_msg)

        parts = []

        # Market Cap (handles Cr, Crore, Lk, Lakh, M, B, etc.)
        mcap_match = re.search(
            r"(?:market_cap|market\s*cap|mcap)\s*(?:>|>=|<|<=|above|over|exceeding|in excess of|at least|below|under)?\s*([\d,]+(?:\.\d+)?)\s*(k|m|mn|b|bn|l|lk|lac|lakh|lakhs|cr|crore|crores)?",
            cleaned_msg,
        )
        if mcap_match:
            raw_val = mcap_match.group(1).replace(",", "")
            val = float(raw_val)
            unit = (mcap_match.group(2) or "").lower()
            mult = MCAP_UNIT_MULTIPLIER.get(unit, 1.0)
            op = (
                "<"
                if any(w in mcap_match.group(0) for w in ("<", "below", "under", "less than"))
                else ">"
            )
            parts.append(f"market_cap {op} {int(val * mult)}")

        # Moving Averages
        if (
            "close above 50" in cleaned_msg
            or "above 50-day" in cleaned_msg
            or "close > sma50" in cleaned_msg
        ):
            parts.append("close > sma50")
        elif "close below 50" in cleaned_msg or "below 50-day" in cleaned_msg:
            parts.append("close < sma50")

        if (
            "close above 20" in cleaned_msg
            or "above 20-day" in cleaned_msg
            or "close > sma20" in cleaned_msg
        ):
            parts.append("close > sma20")

        # Volume
        vol_match = re.search(
            r"volume\s*(?:>|above|over|at least)\s*([\d,]+)\s*(k|m|l|cr)?", cleaned_msg
        )
        if vol_match:
            raw_val = vol_match.group(1).replace(",", "")
            val = float(raw_val)
            unit = (vol_match.group(2) or "").lower()
            mult = VOLUME_UNIT_MULTIPLIER.get(unit, 1)
            parts.append(f"volume > {int(val * mult)}")
        elif "volume >" in cleaned_msg:
            v = re.search(r"volume\s*>\s*([\d,]+)", cleaned_msg)
            if v:
                parts.append(f"volume > {v.group(1).replace(',', '')}")

        # Price / Close
        px_match = re.search(
            r"(?:price|close)\s*(?:>|above|over)\s*(\d[\d,]*(?:\.\d+)?)\b(?!\s*[-]?\s*day)",
            cleaned_msg,
        )
        if px_match:
            parts.append(f"close > {px_match.group(1).replace(',', '')}")

        # Dividend Yield
        dy_match = re.search(
            r"(?:dividend|yield)\s*(?:>|above|over|at least)\s*(\d+(?:\.\d+)?)%?", cleaned_msg
        )
        if dy_match:
            parts.append(f"dividends_yield_current > {dy_match.group(1)}")

        # RSI
        rsi_match = re.search(r"rsi\s*(?:<|below|under)\s*(\d+)", cleaned_msg)
        if rsi_match:
            parts.append(f"rsi < {rsi_match.group(1)}")

        return " AND ".join(parts) if parts else cleaned_msg


def resolve_llm_provider(backend: str) -> LlmPort:
    return _resolve_llm_provider(backend, RuleBasedLlamaTranslator)


# ---------------------------------------------------------------------------
# Tier 2: Python Predicate Generator & Safe Evaluator
# ---------------------------------------------------------------------------
# The filter text is never exec'd directly: each "<metric> <op> <value>" clause is re-emitted
# as a tiny `match(row)` function and run with empty builtins (see safe_eval_predicate).


def generate_python_predicate(query: str, filter_text: str) -> str:
    """Generate a clean, restricted Python screening function for Tier 2 evaluation."""
    conditions = []
    for cond in filter_text.split(" AND "):
        cond = cond.strip()
        if not cond:
            continue
        m = re.match(r"(\w+)\s*([><]=?|==|!=)\s*(.+)", cond)
        if m:
            left, op, right = m.groups()
            right = right.strip()
            # If right operand is another metric alias
            if re.match(r"^[a-zA-Z_]\w*$", right) and not any(
                right.lower().endswith(u) for u in ("cr", "crore", "lk", "lakh", "k", "m", "b")
            ):
                conditions.append(f"row.get('{left}', 0) {op} row.get('{right}', 0)")
            else:
                # Numeric quantity with optional suffix/unit (e.g. 25000Cr, 500k, 2%)
                try:
                    q = parse_quantity(right, market="india")
                    conditions.append(f"row.get('{left}', 0) {op} {q.value}")
                except (ValueError, TypeError, KeyError):
                    cleaned_num = re.sub(r"(?<=\d),(?=\d)", "", right)
                    try:
                        conditions.append(f"row.get('{left}', 0) {op} {float(cleaned_num)}")
                    except ValueError:
                        conditions.append(f"row.get('{left}', 0) {op} {right!r}")

    if not conditions:
        return "def match(row):\n    return True"

    body = " and \\\n           ".join(conditions)
    return f"def match(row):\n    return {body}"


# ---------------------------------------------------------------------------
# Execution Engine (Point-in-Time & Forward Backtester)
# ---------------------------------------------------------------------------


def run_nl_screener(
    query: str,
    backend: str = "auto",
    asof: str | dt.date | None = None,
    lookback_years: int | None = None,
    backtest: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    llm = resolve_llm_provider(backend)

    today = dt.datetime.now(tz=dt.timezone.utc).date()

    # 1. Resolve Point-in-Time As-Of Date ("5 years ago", a bare year, --asof, --lookback-years)
    asof_date, backtest = resolve_asof(query, asof, lookback_years, backtest, today)
    is_historical = backtest or (asof_date < (today - dt.timedelta(days=60)))

    # 2. Translate Query: free text -> Tier 1 filter -> Tier 2 predicate
    raw_response = llm.complete([{"role": "user", "content": query}])
    filter_text = raw_response.strip().strip('"').strip("'")
    python_predicate = generate_python_predicate(query, filter_text)

    # 3. Load Universe & Historical Bars
    members = resolve_universe("nifty50", exchange="NSE")
    instruments = [InstrumentId(sym.symbol, "NSE") for sym in members]

    interval = DateInterval(dt.date(2015, 1, 1), dt.date(2027, 1, 1))
    metrics_to_extract = ["close", "volume", "sma20", "sma50", "rsi"]

    asof_ns = int(
        dt.datetime.combine(asof_date, dt.time.max, tzinfo=dt.timezone.utc).timestamp() * 1e9
    )

    matched_rows = []
    total_valid_instruments = 0
    term_date = asof_date

    for inst in instruments:
        all_bars = _STORE.read(inst, "1D", interval)
        if not all_bars:
            continue

        # Point-in-time slice strictly up to asof_date (zero lookahead) -> metrics row
        built = build_point_in_time_row(inst.symbol, all_bars, asof_ns, metrics_to_extract)
        if built is None:
            continue
        row, entry_close = built
        total_valid_instruments += 1

        if safe_eval_predicate(python_predicate, row):
            # Forward performance simulation up to today
            if is_historical:
                term_bar = all_bars[-1]
                term_date = dt.datetime.fromtimestamp(term_bar.ts / 1e9, tz=dt.timezone.utc).date()
                row.update(
                    forward_performance(entry_close, float(term_bar.close), asof_date, term_date)
                )

            matched_rows.append(row)

    # Sort
    if is_historical:
        matched_rows.sort(key=lambda r: r.get("return_pct", 0), reverse=True)
    else:
        matched_rows.sort(key=lambda r: r.get("volume", 0), reverse=True)

    basket_stats = basket_statistics(matched_rows, asof_date, term_date) if is_historical else {}

    return {
        "query": query,
        "backend": type(llm).__name__,
        "asof": str(asof_date),
        "is_historical": is_historical,
        "filter_text": filter_text,
        "python_predicate": python_predicate,
        "matched_count": len(matched_rows),
        "total_scanned": total_valid_instruments,
        "basket_stats": basket_stats,
        "rows": matched_rows[:limit],
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    add_nl_screener_args(parser)
    args = parser.parse_args(argv)

    res = run_nl_screener(
        args.query,
        backend=args.backend,
        asof=args.asof,
        lookback_years=args.lookback_years,
        backtest=args.backtest,
        limit=args.limit,
    )

    is_table = (args.format == "table") or (args.format == "auto" and sys.stdout.isatty())
    if is_table:
        render_nl_result(res, args.query)
    else:
        print(json.dumps(res, indent=2, default=str))

    return 0


if __name__ == "__main__":
    sys.exit(main())
