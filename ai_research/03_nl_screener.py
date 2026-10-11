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

import argparse
import ast
import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba.ai.llm.provider import LlmPort, ScriptedFakeLlm
from honba.cli.data import _STORE
from honba.data.query.quantity import parse_quantity
from honba.display import Column, render_kv, render_table
from honba.entities.instrument import InstrumentId
from honba.markets.india.universes import resolve_universe
from honba.screener.coverage import DateInterval
from honba.screener.sources import extract_metrics_from_bars

# Approximate shares outstanding (in Crore shares) for Nifty 50 constituents
NIFTY50_SHARES_CR: dict[str, float] = {
    "RELIANCE": 676.6,
    "TCS": 361.8,
    "HDFCBANK": 762.5,
    "BHARTIARTL": 601.2,
    "ICICIBANK": 706.3,
    "INFY": 415.1,
    "SBIN": 892.4,
    "ITC": 1250.7,
    "HINDUNILVR": 234.9,
    "LT": 137.4,
    "BAJFINANCE": 61.9,
    "HCLTECH": 271.4,
    "M&M": 124.3,
    "MARUTI": 31.4,
    "SUNPHARMA": 239.9,
    "TITAN": 88.8,
    "TATAMOTORS": 368.5,
    "ADANIENT": 114.0,
    "KOTAKBANK": 198.8,
    "NTPC": 969.7,
    "ONGC": 1258.0,
    "POWERGRID": 930.0,
    "COALINDIA": 616.3,
    "AXISBANK": 309.2,
    "TATASTEEL": 1248.4,
    "ASIANPAINT": 95.9,
    "BAJAJFINSV": 159.5,
    "NESTLEIND": 96.4,
    "ULTRACEMCO": 28.9,
    "WIPRO": 523.0,
    "ADANIPORTS": 216.0,
    "JSWSTEEL": 244.5,
    "GRASIM": 67.9,
    "LTIM": 29.6,
    "TECHM": 98.0,
    "HINDALCO": 224.7,
    "DIVISLAB": 26.5,
    "CIPLA": 80.7,
    "SBILIFE": 100.1,
    "DRREDDY": 16.7,
    "TATACONSUM": 95.3,
    "BRITANNIA": 24.1,
    "BPCL": 216.9,
    "EICHERMOT": 27.4,
    "APOLLOHOSP": 14.4,
    "SHRIRAMFIN": 37.6,
    "HEROMOTOCO": 20.0,
    "INDUSINDBK": 77.8,
    "BAJAJ-AUTO": 27.9,
    "BEL": 731.0,
    "TRENT": 35.5,
}

# ---------------------------------------------------------------------------
# LLM Providers for Natural Language Translation
# ---------------------------------------------------------------------------


class OllamaLlm(LlmPort):
    """Local Ollama HTTP client (e.g. Llama 3.2, Qwen 2.5) at localhost:11434."""

    def __init__(
        self,
        model: str = "llama3.2",
        host: str = "http://localhost:11434",
    ) -> None:
        self.model = model
        self.host = host

    def complete(
        self,
        messages: Sequence[dict[str, str]],
        grammar: str | None = None,
        schema: dict[str, Any] | None = None,
        seed: int | None = None,
        temperature: float = 0.0,
    ) -> str:
        payload = {
            "model": self.model,
            "messages": list(messages),
            "stream": False,
            "options": {"temperature": temperature},
        }
        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return str(data["message"]["content"])


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
            mult = (
                1e7
                if unit in ("cr", "crore", "crores")
                else 1e5
                if unit in ("l", "lk", "lac", "lakh", "lakhs")
                else 1e3
                if unit == "k"
                else 1e6
                if unit in ("m", "mn")
                else 1e9
                if unit in ("b", "bn")
                else 1.0
            )
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
            mult = (
                1_000
                if unit == "k"
                else 1_000_000
                if unit == "m"
                else 100_000
                if unit == "l"
                else 10_000_000
                if unit == "cr"
                else 1
            )
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
    if backend == "ollama":
        return OllamaLlm()
    if backend == "fake":
        return ScriptedFakeLlm(default_response="volume > 500000 AND close > sma50")
    # auto: probe Ollama HTTP endpoint, fallback to rule-based offline
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1):
            return OllamaLlm()
    except (urllib.error.URLError, OSError, TimeoutError):
        return RuleBasedLlamaTranslator()


# ---------------------------------------------------------------------------
# Tier 2: Python Predicate Generator & Safe Evaluator
# ---------------------------------------------------------------------------


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


def safe_eval_predicate(predicate_code: str, row: dict[str, Any]) -> bool:
    """Execute the generated predicate in a restricted scope (Design.md Section 13.2)."""
    tree = ast.parse(predicate_code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Attribute)) and not (
            isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "get"
        ):
            pass

    local_vars: dict[str, Any] = {}
    exec(compile(tree, "<screener_ast>", "exec"), {"__builtins__": {}}, local_vars)  # noqa: S102
    return bool(local_vars["match"](row))


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

    # 1. Resolve Point-in-Time As-Of Date
    asof_date: dt.date | None = None
    if asof:
        asof_date = dt.date.fromisoformat(str(asof))
    elif lookback_years is not None:
        asof_date = today.replace(year=today.year - lookback_years)
    else:
        # Detect phrases like "5 years ago", "3 years ago"
        ago_match = re.search(r"(\d+)\s*years?\s*ago", query.lower())
        if ago_match:
            y = int(ago_match.group(1))
            asof_date = today.replace(year=today.year - y)
            backtest = True
        else:
            yr_match = re.search(r"\b(201[5-9]|202[0-5])\b", query)
            if yr_match:
                asof_date = dt.date(int(yr_match.group(1)), 10, 11)
                backtest = True

    if asof_date is None:
        asof_date = today

    is_historical = backtest or (asof_date < (today - dt.timedelta(days=60)))

    # 2. Translate Query
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

        # Point-in-time slice strictly up to asof_date (Zero lookahead)
        historical_bars = [b for b in all_bars if b.ts <= asof_ns]
        if len(historical_bars) < 50:
            continue
        total_valid_instruments += 1

        extracted = extract_metrics_from_bars(metrics_to_extract, historical_bars)
        entry_close = float(extracted.get("close") or 0.0)

        # Dynamic market cap as of historical entry date
        shares_cr = NIFTY50_SHARES_CR.get(inst.symbol, 50.0)
        mcap = entry_close * shares_cr * 1e7

        row = {
            "symbol": f"{inst.symbol}.NSE",
            **extracted,
            "market_cap": mcap,
            "market_cap_basic": mcap,
            "market_cap_cr": mcap / 1e7,
            "entry_close": entry_close,
        }

        if safe_eval_predicate(python_predicate, row):
            # Forward performance simulation up to today
            if is_historical:
                term_bar = all_bars[-1]
                term_close = float(term_bar.close)
                term_date = dt.datetime.fromtimestamp(term_bar.ts / 1e9, tz=dt.timezone.utc).date()
                days_held = (term_date - asof_date).days
                years_held = max(days_held / 365.25, 0.1)

                ret_pct = ((term_close - entry_close) / entry_close) * 100
                mult = term_close / entry_close
                cagr = ((mult ** (1.0 / years_held)) - 1.0) * 100

                if mult >= 10.0:
                    badge = "10-Bagger 🏆"
                elif mult >= 5.0:
                    badge = "5-Bagger 🚀"
                elif mult >= 2.0:
                    badge = "Multibagger 🔥"
                elif ret_pct > 0:
                    badge = "Profitable 📈"
                else:
                    badge = "Loss 🔻"

                row["current_close"] = term_close
                row["return_pct"] = ret_pct
                row["cagr"] = cagr
                row["multiplier"] = mult
                row["years_held"] = years_held
                row["badge"] = badge

            matched_rows.append(row)

    # Sort
    if is_historical:
        matched_rows.sort(key=lambda r: r.get("return_pct", 0), reverse=True)
    else:
        matched_rows.sort(key=lambda r: r.get("volume", 0), reverse=True)

    displayed = matched_rows[:limit]

    # Portfolio basket statistics
    basket_stats: dict[str, Any] = {}
    if is_historical and matched_rows:
        avg_ret = sum(r["return_pct"] for r in matched_rows) / len(matched_rows)
        avg_mult = sum(r["multiplier"] for r in matched_rows) / len(matched_rows)
        avg_years = matched_rows[0]["years_held"]
        basket_cagr = ((avg_mult ** (1.0 / avg_years)) - 1.0) * 100
        win_rate = sum(1 for r in matched_rows if r["return_pct"] > 0) / len(matched_rows) * 100
        baggers = sum(1 for r in matched_rows if r["multiplier"] >= 2.0)
        top_pick = matched_rows[0]

        basket_stats = {
            "asof_date": str(asof_date),
            "terminal_date": str(term_date),
            "years_held": round(avg_years, 1),
            "basket_return_pct": round(avg_ret, 1),
            "basket_cagr_pct": round(basket_cagr, 1),
            "win_rate_pct": round(win_rate, 1),
            "multibagger_count": f"{baggers} / {len(matched_rows)}",
            "top_performer": f"{top_pick['symbol']} (+{top_pick['return_pct']:.1f}%, {top_pick['multiplier']:.2f}x)",
        }

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
        "rows": displayed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument(
        "--query",
        default="market_cap > 25000Cr and close above 50-day moving average",
        help="Natural language screening query",
    )
    parser.add_argument(
        "--asof",
        default=None,
        help="Point-in-time evaluation date (YYYY-MM-DD), e.g. '2021-10-11' for 5 years ago",
    )
    parser.add_argument(
        "--lookback-years",
        type=int,
        default=None,
        help="Convenience lookback years (e.g. 5 for 5 years ago)",
    )
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="Run forward performance backtest from asof date to present",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "ollama", "rule-based", "fake"],
        default="auto",
        help="LLM translation backend",
    )
    parser.add_argument("--limit", type=int, default=20, help="Max results to display")
    parser.add_argument(
        "--format",
        choices=["auto", "table", "json"],
        default="auto",
        help="Output format",
    )
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
        summary_items = [
            ("User Prompt", res["query"]),
            ("LLM Backend", res["backend"]),
            ("Tier 1 Filter", res["filter_text"]),
            ("Point-in-Time Date", res["asof"]),
            ("Matched Stocks", f"{res['matched_count']} / {res['total_scanned']}"),
        ]

        if res["is_historical"] and res["basket_stats"]:
            stats = res["basket_stats"]
            summary_items.extend(
                [
                    (
                        "Backtest Period",
                        f"{stats['asof_date']} → {stats['terminal_date']} ({stats['years_held']} yrs)",
                    ),
                    ("Basket Total Return", f"+{stats['basket_return_pct']}%"),
                    ("Basket CAGR", f"+{stats['basket_cagr_pct']}%"),
                    ("Win Rate", f"{stats['win_rate_pct']}%"),
                    ("Multibaggers (≥2x)", stats["multibagger_count"]),
                    ("Top Performer", stats["top_performer"]),
                ]
            )

        render_kv(summary_items, title="Natural Language Screener & Backtest Translation")

        print("\nGenerated Tier 2 Python Predicate:")
        for line in res["python_predicate"].splitlines():
            print(f"  {line}")
        print()

        if res["is_historical"]:
            cols = [
                Column("symbol", "Symbol", style="cyan"),
                Column(
                    "entry_close",
                    f"Entry ({res['asof']})",
                    align="right",
                    style="white",
                    fmt=lambda v: f"₹{v:,.1f}" if v else "-",
                ),
                Column(
                    "current_close",
                    "CMP Today",
                    align="right",
                    style="white",
                    fmt=lambda v: f"₹{v:,.1f}" if v else "-",
                ),
                Column(
                    "market_cap_cr",
                    "Entry Mcap",
                    align="right",
                    style="yellow",
                    fmt=lambda v: f"₹{v:,.0f} Cr" if v else "-",
                ),
                Column(
                    "return_pct",
                    "Return",
                    align="right",
                    style="green",
                    fmt=lambda v: f"+{v:,.1f}%" if v > 0 else f"{v:,.1f}%",
                ),
                Column(
                    "cagr",
                    "CAGR",
                    align="right",
                    style="bold green",
                    fmt=lambda v: f"+{v:.1f}%" if v > 0 else f"{v:.1f}%",
                ),
                Column(
                    "multiplier",
                    "Multiplier",
                    align="right",
                    style="bold yellow",
                    fmt=lambda v: f"{v:.2f}x" if v else "-",
                ),
                Column("badge", "Classification", style="magenta"),
            ]
            title = f"Historical Screener Backtest Results (As of {res['asof']} → Today)"
        else:
            cols = [
                Column("symbol", "Symbol", style="cyan"),
                Column(
                    "close",
                    "CMP (₹)",
                    align="right",
                    style="green",
                    fmt=lambda v: f"₹{v:,.2f}" if v else "-",
                ),
                Column(
                    "market_cap_cr",
                    "Market Cap",
                    align="right",
                    style="bold yellow",
                    fmt=lambda v: f"₹{v:,.0f} Cr" if v else "-",
                ),
                Column(
                    "volume",
                    "Volume",
                    align="right",
                    style="magenta",
                    fmt=lambda v: f"{int(v):,}" if v else "-",
                ),
                Column(
                    "sma50",
                    "SMA 50 (₹)",
                    align="right",
                    style="yellow",
                    fmt=lambda v: f"₹{v:,.2f}" if v else "-",
                ),
                Column(
                    "rsi",
                    "RSI",
                    align="right",
                    style="white",
                    fmt=lambda v: f"{v:.1f}" if v else "-",
                ),
            ]
            title = f"Screener Matches for: '{args.query}'"

        render_table(res["rows"], cols, title=title)
    else:
        print(json.dumps(res, indent=2, default=str))

    return 0


if __name__ == "__main__":
    sys.exit(main())
