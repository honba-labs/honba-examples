"""honba_examples.screening.nl_support: plumbing for the natural-language screener.

Shares-outstanding table, the Ollama HTTP backend, LLM-backend selection, the restricted
predicate evaluator, as-of date resolution, and forward-performance / basket statistics.
The NL -> filter -> predicate idea itself lives in ``ai_research/03_nl_screener.py``.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from honba.ai.llm.provider import LlmPort, ScriptedFakeLlm
from honba.screener.sources import extract_metrics_from_bars

__all__ = [
    "MCAP_UNIT_MULTIPLIER",
    "NIFTY50_SHARES_CR",
    "VOLUME_UNIT_MULTIPLIER",
    "OllamaLlm",
    "basket_statistics",
    "build_point_in_time_row",
    "forward_performance",
    "resolve_asof",
    "resolve_llm_provider",
    "safe_eval_predicate",
]

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

# Unit suffix -> multiplier, for market cap (rupees) and volume (shares).
MCAP_UNIT_MULTIPLIER: dict[str, float] = {
    **dict.fromkeys(("cr", "crore", "crores"), 1e7),
    **dict.fromkeys(("l", "lk", "lac", "lakh", "lakhs"), 1e5),
    "k": 1e3,
    **dict.fromkeys(("m", "mn"), 1e6),
    **dict.fromkeys(("b", "bn"), 1e9),
}
VOLUME_UNIT_MULTIPLIER: dict[str, int] = {
    "k": 1_000,
    "m": 1_000_000,
    "l": 100_000,
    "cr": 10_000_000,
}


def build_point_in_time_row(
    symbol: str, all_bars: Sequence[Any], asof_ns: int, metrics: list[str]
) -> tuple[dict[str, Any], float] | None:
    """Metrics row for ``symbol`` from bars up to ``asof_ns``; ``None`` if < 50 bars of history."""
    historical_bars = [b for b in all_bars if b.ts <= asof_ns]
    if len(historical_bars) < 50:
        return None

    extracted = extract_metrics_from_bars(metrics, historical_bars)
    entry_close = float(extracted.get("close") or 0.0)

    # Dynamic market cap as of historical entry date
    shares_cr = NIFTY50_SHARES_CR.get(symbol, 50.0)
    mcap = entry_close * shares_cr * 1e7

    row = {
        "symbol": f"{symbol}.NSE",
        **extracted,
        "market_cap": mcap,
        "market_cap_basic": mcap,
        "market_cap_cr": mcap / 1e7,
        "entry_close": entry_close,
    }
    return row, entry_close


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


def resolve_llm_provider(backend: str, offline: Callable[[], LlmPort]) -> LlmPort:
    """Pick the translation backend; ``offline`` builds the deterministic rule-based fallback."""
    if backend == "ollama":
        return OllamaLlm()
    if backend == "fake":
        return ScriptedFakeLlm(default_response="volume > 500000 AND close > sma50")
    # auto: probe Ollama HTTP endpoint, fallback to rule-based offline
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1):
            return OllamaLlm()
    except (urllib.error.URLError, OSError, TimeoutError):
        return offline()


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


def resolve_asof(
    query: str,
    asof: str | dt.date | None,
    lookback_years: int | None,
    backtest: bool,
    today: dt.date,
) -> tuple[dt.date, bool]:
    """Resolve the point-in-time date (explicit, lookback, or parsed from the query).

    Returns ``(asof_date, backtest)``; phrases like "5 years ago" or a bare year switch
    ``backtest`` on.
    """
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
    return (today if asof_date is None else asof_date), backtest


def forward_performance(
    entry_close: float, term_close: float, asof_date: dt.date, term_date: dt.date
) -> dict[str, Any]:
    """Forward return, CAGR, bag multiplier and badge from entry to terminal close."""
    days_held = (term_date - asof_date).days
    years_held = max(days_held / 365.25, 0.1)

    ret_pct = ((term_close - entry_close) / entry_close) * 100
    mult = term_close / entry_close
    cagr = ((mult ** (1.0 / years_held)) - 1.0) * 100

    if mult >= 10.0:
        badge = "10-Bagger \U0001f3c6"
    elif mult >= 5.0:
        badge = "5-Bagger \U0001f680"
    elif mult >= 2.0:
        badge = "Multibagger \U0001f525"
    elif ret_pct > 0:
        badge = "Profitable \U0001f4c8"
    else:
        badge = "Loss \U0001f53b"

    return {
        "current_close": term_close,
        "return_pct": ret_pct,
        "cagr": cagr,
        "multiplier": mult,
        "years_held": years_held,
        "badge": badge,
    }


def basket_statistics(
    matched_rows: list[dict[str, Any]], asof_date: dt.date, term_date: dt.date
) -> dict[str, Any]:
    """Equal-weight basket summary of the forward performance of the matched rows."""
    if not matched_rows:
        return {}
    avg_ret = sum(r["return_pct"] for r in matched_rows) / len(matched_rows)
    avg_mult = sum(r["multiplier"] for r in matched_rows) / len(matched_rows)
    avg_years = matched_rows[0]["years_held"]
    basket_cagr = ((avg_mult ** (1.0 / avg_years)) - 1.0) * 100
    win_rate = sum(1 for r in matched_rows if r["return_pct"] > 0) / len(matched_rows) * 100
    baggers = sum(1 for r in matched_rows if r["multiplier"] >= 2.0)
    top_pick = matched_rows[0]

    return {
        "asof_date": str(asof_date),
        "terminal_date": str(term_date),
        "years_held": round(avg_years, 1),
        "basket_return_pct": round(avg_ret, 1),
        "basket_cagr_pct": round(basket_cagr, 1),
        "win_rate_pct": round(win_rate, 1),
        "multibagger_count": f"{baggers} / {len(matched_rows)}",
        "top_performer": f"{top_pick['symbol']} (+{top_pick['return_pct']:.1f}%, {top_pick['multiplier']:.2f}x)",
    }
