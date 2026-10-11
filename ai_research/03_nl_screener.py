"""ai_research/03_nl_screener: End-to-end Natural Language Stock Screener.

Translates free-text trader queries into validated filter expressions and executable
Python predicates, then executes them across the local stock universe (Parquet bars & fundamentals).

Flow:
    Natural Language Query
         ↓
    Local Llama / LLM (KnowledgePack guided)
         ↓
    [Tier 1] Filter Syntax: `volume > 500000 AND close > sma50`
    [Tier 2] Python Predicate: `def match(row): return ...`
         ↓
    Evaluator (Parquet OHLCV store + fundamental metrics)
         ↓
    Interactive Rich Table Results (CMP, Market Cap, Volume, Indicators, Yield)

Run::

    python ai_research/03_nl_screener.py --query "market_cap > 25000Cr and close above 50-day moving average"
    python ai_research/03_nl_screener.py --query "volume > 500k and close above 50-day moving average"
    python ai_research/03_nl_screener.py --query "dividend yield > 2% and price > 200"
    python ai_research/03_nl_screener.py --format json
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

        parts = []

        # Market Cap (handles Cr, Crore, Lk, Lakh, M, B, etc.)
        mcap_match = re.search(
            r"(?:market_cap|market\s*cap|mcap)\s*(?:>|>=|<|<=|above|over|exceeding|in excess of|at least|below|under)?\s*([\d,]+(?:\.\d+)?)\s*(k|m|mn|b|bn|l|lk|lac|lakh|lakhs|cr|crore|crores)?",
            user_msg,
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
            "close above 50" in user_msg
            or "above 50-day" in user_msg
            or "close > sma50" in user_msg
        ):
            parts.append("close > sma50")
        elif "close below 50" in user_msg or "below 50-day" in user_msg:
            parts.append("close < sma50")

        if (
            "close above 20" in user_msg
            or "above 20-day" in user_msg
            or "close > sma20" in user_msg
        ):
            parts.append("close > sma20")

        # Volume
        vol_match = re.search(
            r"volume\s*(?:>|above|over|at least)\s*([\d,]+)\s*(k|m|l|cr)?", user_msg
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
        elif "volume >" in user_msg:
            v = re.search(r"volume\s*>\s*([\d,]+)", user_msg)
            if v:
                parts.append(f"volume > {v.group(1).replace(',', '')}")

        # Price / Close
        px_match = re.search(
            r"(?:price|close)\s*(?:>|above|over)\s*(\d[\d,]*(?:\.\d+)?)\b(?!\s*[-]?\s*day)",
            user_msg,
        )
        if px_match:
            parts.append(f"close > {px_match.group(1).replace(',', '')}")

        # Dividend Yield
        dy_match = re.search(
            r"(?:dividend|yield)\s*(?:>|above|over|at least)\s*(\d+(?:\.\d+)?)%?", user_msg
        )
        if dy_match:
            parts.append(f"dividends_yield_current > {dy_match.group(1)}")

        # RSI
        rsi_match = re.search(r"rsi\s*(?:<|below|under)\s*(\d+)", user_msg)
        if rsi_match:
            parts.append(f"rsi < {rsi_match.group(1)}")

        return " AND ".join(parts) if parts else user_msg


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
# Execution Engine
# ---------------------------------------------------------------------------


def run_nl_screener(
    query: str,
    backend: str = "auto",
    limit: int = 20,
) -> dict[str, Any]:
    llm = resolve_llm_provider(backend)

    # 1. Translate Query
    raw_response = llm.complete([{"role": "user", "content": query}])
    filter_text = raw_response.strip().strip('"').strip("'")
    python_predicate = generate_python_predicate(query, filter_text)

    # 2. Load Local Universe Data
    members = resolve_universe("nifty50", exchange="NSE")
    instruments = [InstrumentId(sym.symbol, "NSE") for sym in members]

    interval = DateInterval(dt.date(2024, 1, 1), dt.date(2026, 12, 31))
    metrics_to_extract = ["close", "volume", "sma20", "sma50", "rsi"]

    matched_rows = []
    for inst in instruments:
        bars = _STORE.read(inst, "1D", interval)
        if not bars:
            continue

        extracted = extract_metrics_from_bars(metrics_to_extract, bars)
        row = {"symbol": f"{inst.symbol}.NSE", **extracted}

        # Calculate dynamic market cap based on shares outstanding
        shares_cr = NIFTY50_SHARES_CR.get(inst.symbol, 50.0)
        close_px = float(row.get("close") or 0.0)
        mcap = close_px * shares_cr * 1e7
        row["market_cap"] = mcap
        row["market_cap_basic"] = mcap
        row["market_cap_cr"] = mcap / 1e7

        if safe_eval_predicate(python_predicate, row):
            matched_rows.append(row)

    matched_rows.sort(key=lambda r: r.get("volume", 0), reverse=True)
    displayed = matched_rows[:limit]

    return {
        "query": query,
        "backend": type(llm).__name__,
        "filter_text": filter_text,
        "python_predicate": python_predicate,
        "matched_count": len(matched_rows),
        "total_scanned": len(instruments),
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

    res = run_nl_screener(args.query, backend=args.backend, limit=args.limit)

    is_table = (args.format == "table") or (args.format == "auto" and sys.stdout.isatty())

    if is_table:
        render_kv(
            [
                ("User Prompt", res["query"]),
                ("LLM Backend", res["backend"]),
                ("Tier 1 Filter", res["filter_text"]),
                ("Matched Stocks", f"{res['matched_count']} / {res['total_scanned']}"),
            ],
            title="Natural Language Screener Translation",
        )

        print("\nGenerated Tier 2 Python Predicate:")
        for line in res["python_predicate"].splitlines():
            print(f"  {line}")
        print()

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
        render_table(res["rows"], cols, title=f"Screener Matches for: '{args.query}'")
    else:
        print(json.dumps(res, indent=2, default=str))

    return 0


if __name__ == "__main__":
    sys.exit(main())
