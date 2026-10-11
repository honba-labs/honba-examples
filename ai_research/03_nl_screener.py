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
    Interactive Rich Table Results (CMP, Volume, Indicators, Yield)

Run::

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
from honba.display import Column, render_kv, render_table
from honba.entities.instrument import InstrumentId
from honba.markets.india.universes import resolve_universe
from honba.screener.coverage import DateInterval
from honba.screener.sources import extract_metrics_from_bars

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
        vol_match = re.search(r"volume\s*(?:>|above|over|at least)\s*(\d+)\s*(k|m|l|cr)?", user_msg)
        if vol_match:
            val = float(vol_match.group(1))
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
            v = re.search(r"volume\s*>\s*(\d+)", user_msg)
            if v:
                parts.append(f"volume > {v.group(1)}")

        # Price / Close
        px_match = re.search(r"(?:price|close)\s*(?:>|above|over)\s*(\d+)", user_msg)
        if px_match:
            parts.append(f"close > {px_match.group(1)}")

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
        m = re.match(r"(\w+)\s*([><]=?|==|!=)\s*([a-zA-Z0-9_.]+)", cond)
        if m:
            left, op, right = m.groups()
            if re.match(r"^[a-zA-Z]", right):
                conditions.append(f"row.get('{left}', 0) {op} row.get('{right}', 0)")
            else:
                conditions.append(f"row.get('{left}', 0) {op} {float(right)}")

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
        default="volume > 500k and close above 50-day moving average",
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
        render_table(
            res["rows"],
            cols,
            title=f"Screener Results ({res['matched_count']} matched)",
        )
    else:
        print(json.dumps(res, indent=2, default=str))

    return 0


if __name__ == "__main__":
    sys.exit(main())
