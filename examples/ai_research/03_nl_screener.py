"""ai_research/03_nl_screener: End-to-end Natural Language Stock Screener & Historical Backtester.

Translates free-text trader queries into validated filter expressions and executable
Python predicates, then executes them across the local stock universe (Parquet bars & fundamentals).

Supports:
1. Latest Market Screening: Evaluates live/latest bar snapshot.
2. Point-in-Time Historical Screening (`--asof`, an ISO date, "in 2020" or "5 years ago"): Slices bar history to that
   exact date without lookahead bias.
3. Forward Backtesting & Multibagger Tracking: For historical screens, tracks the forward
   performance of every matched stock up to today, computing Total Return %, CAGR %, and
   Bag Multipliers (2x, 5x, 10x).

Run::

    # Point-in-time screen 5 years ago with forward multibagger backtest
    python examples/ai_research/03_nl_screener.py --asof 2021-10-11 --query "market_cap > 25000Cr and close above 50-day moving average"

    # Natural language historical backtest query
    python examples/ai_research/03_nl_screener.py --query "close above 50-day moving average and volume > 500k 5 years ago"

    # Latest live screen
    python examples/ai_research/03_nl_screener.py --query "volume > 500k and rsi < 40"

Pitfalls::

    - Survivorship and hindsight in the output: the universe is resolve_universe("nifty50") with
      no as_of, today's members screened at a past date. market_cap is the as-of close times one
      fixed, approximate share-count table with no per-date history (50 crore if a symbol is
      missing); the output's notes list says so. Historical rows are ordered by as-of market cap
      before --limit, and the forward returns (rows, basket_stats, forward_returns) are ex post
      hindsight with 2x/5x/10x badges from the future price. Read matched_count and basket_stats.
    - Strict predicates: generate_python_predicate raises on an empty filter, on clauses its
      regex does not match, and on a metric outside KNOWN_METRICS (close, volume, sma20, sma50,
      rsi and the market cap keys; for example dividend yield is rejected), and a known metric
      missing from a row makes the predicate False. A filter that parses can still be the wrong
      filter: compare filter_text and python_predicate in the output with your question.
    - Execution safety: the filter text is never exec'd. Each clause is re-emitted as a tiny
      match(row) function over bare metric names, and safe_eval_predicate only evaluates it after
      a whitelist AST walk (comparisons, and/or/not, + - * / %, numeric constants, known names;
      no calls, attributes, subscripts, lambdas or strings), with empty builtins. Keep it that
      way; do not widen the whitelist to accept model-written code.
    - Backend and non-determinism: --backend auto probes localhost:11434 and silently switches
      between Ollama and the regex translator, so one query can give different filters on
      different machines. The Ollama call sends the query as the only message (no system prompt
      or grammar), ignores seed and uses an unpinned model tag; the fake backend returns the same
      filter for every query. The backend is recorded in the output; keep it with the result.
    - Query parsing hazards: the as-of date comes only from date phrasing ("N years ago", an ISO
      date, "in 2020", "as of 2020"), so "volume > 2020" is a volume. "N years ago" counts from
      today, so the same sentence means a different date tomorrow, and a bare year means 11
      October of that year. The query is untrusted text; only the clause regexes and the
      predicate whitelist stand between it and the evaluator.
    - Terminal price is each symbol's last bar, so a symbol whose data ends early is scored on a
      stale close, and a screen of about 50 names gives small, unstable baskets.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from collections.abc import Sequence
from typing import Any

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
    KNOWN_METRICS,
    MCAP_UNIT_MULTIPLIER,
    NIFTY50_SHARES_CR,
    VOLUME_UNIT_MULTIPLIER,
    OllamaLlm,
    PredicateError,
    UnknownMetricError,
    UnparsedClausesError,
    basket_statistics,
    build_point_in_time_row,
    forward_performance,
    market_cap_notes,
    resolve_asof,
    safe_eval_predicate,
)
from honba_examples.screening.nl_support import resolve_llm_provider as _resolve_llm_provider

__all__ = [
    "KNOWN_METRICS",
    "NIFTY50_SHARES_CR",
    "OllamaLlm",
    "PredicateError",
    "RuleBasedLlamaTranslator",
    "UnknownMetricError",
    "UnparsedClausesError",
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


_CLAUSE_RE = re.compile(r"^(\w+)\s*(>=|<=|==|!=|>|<)\s*(.+)$")
_IDENT_RE = re.compile(r"^[A-Za-z_]\w*$")


def generate_python_predicate(query: str, filter_text: str) -> str:
    """Generate a restricted ``def match(row): return <expr>`` for Tier 2 evaluation.

    Strict: every clause must parse and every metric must be in ``KNOWN_METRICS``.

    Raises:
        UnknownMetricError: a metric name the point-in-time row does not provide.
        UnparsedClausesError: clauses that are not ``<metric> <op> <number or metric>``.
        PredicateError: the filter text has no clauses at all (it would match everything).
    """
    raw_clauses = [
        c.strip() for c in re.split(r"\s+AND\s+", filter_text.strip(), flags=re.IGNORECASE)
    ]
    clauses = [c for c in raw_clauses if c]
    if not clauses:
        raise PredicateError(
            f"no clauses in filter {filter_text!r} for query {query!r}; refusing an "
            "always-true predicate"
        )

    conditions: list[str] = []
    unparsed: list[str] = []
    unknown: list[str] = []
    for cond in clauses:
        m = _CLAUSE_RE.match(cond)
        if not m:
            unparsed.append(cond)
            continue
        left, op, right = m.group(1).lower(), m.group(2), m.group(3).strip()
        if left not in KNOWN_METRICS:
            unknown.append(left)
            continue
        if _IDENT_RE.match(right):
            if right.lower() not in KNOWN_METRICS:
                unknown.append(right)
                continue
            conditions.append(f"{left} {op} {right.lower()}")
            continue
        number: float | None = None
        try:
            number = float(parse_quantity(right, market="india").value)
        except (ValueError, TypeError, KeyError):
            try:
                number = float(re.sub(r"(?<=\d),(?=\d)", "", right))
            except ValueError:
                unparsed.append(cond)
        if number is not None:
            conditions.append(f"{left} {op} {number!r}")

    if unknown:
        raise UnknownMetricError(
            f"unknown metric {', '.join(dict.fromkeys(unknown))}; known: {sorted(KNOWN_METRICS)}"
        )
    if unparsed:
        raise UnparsedClausesError(unparsed)

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

    # Sort by something known at the as-of date, never by the forward return: --limit would
    # otherwise keep only the winners. Forward returns are reported separately below.
    if is_historical:
        matched_rows.sort(key=lambda r: (-r.get("market_cap", 0), r["symbol"]))
    else:
        matched_rows.sort(key=lambda r: (-r.get("volume", 0), r["symbol"]))

    basket_stats = basket_statistics(matched_rows, asof_date, term_date) if is_historical else {}
    notes = market_cap_notes()
    forward_returns: list[dict[str, Any]] = []
    if is_historical:
        notes.append(
            "universe is today's Nifty 50 (survivorship-biased); rows are ordered by as-of market "
            "cap, and forward_returns (all matches, best first) is hindsight, not a signal."
        )
        forward_returns = sorted(
            (
                {
                    "symbol": r["symbol"],
                    "return_pct": r["return_pct"],
                    "multiplier": r["multiplier"],
                }
                for r in matched_rows
            ),
            key=lambda x: -x["return_pct"],
        )

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
        "forward_returns": forward_returns,
        "notes": notes,
        "rows": matched_rows[:limit],
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    add_nl_screener_args(parser)
    args = parser.parse_args(argv)

    try:
        res = run_nl_screener(
            args.query,
            backend=args.backend,
            asof=args.asof,
            lookback_years=args.lookback_years,
            backtest=args.backtest,
            limit=args.limit,
        )
    except PredicateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    is_table = (args.format == "table") or (args.format == "auto" and sys.stdout.isatty())
    if is_table:
        render_nl_result(res, args.query)
    else:
        print(json.dumps(res, indent=2, default=str))

    return 0


if __name__ == "__main__":
    sys.exit(main())
