from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def bars_digest(bars: Sequence[Any]) -> str:
    """Return deterministic SHA256 digest over bars."""
    m = hashlib.sha256()

    def _key(b: Any) -> tuple[str, int]:
        iid = getattr(b, "instrument_id", None)
        sym = getattr(iid, "symbol", None) if iid else getattr(b, "symbol", "")
        return (str(sym), getattr(b, "ts", 0))

    for b in sorted(bars, key=_key):
        iid = getattr(b, "instrument_id", None)
        sym = getattr(iid, "symbol", None) if iid else getattr(b, "symbol", "")
        exch = getattr(iid, "exchange", "") if iid else ""
        ts = getattr(b, "ts", None)
        if ts is None and isinstance(b, dict):
            ts = b.get("ts") or b.get("timestamp") or b.get("time")
        ts = ts or 0
        o = getattr(b, "open", None)
        if o is not None:
            h_ = getattr(b, "high", None)
            l = getattr(b, "low", None)
            c = getattr(b, "close", None)
            v = getattr(b, "volume", None)
            m.update(f"{sym}|{exch}|{ts}|{o!r}|{h_!r}|{l!r}|{c!r}|{v!r}\n".encode())
        else:
            m.update(f"{ts}|{sym}\n".encode())
    return m.hexdigest()


def resolve_window(
    test_start: dt.date,
    test_end: dt.date | None,
    warmup_days: int,
    load_fn=None,
    *,
    require_full_coverage: bool = False,
) -> tuple[dt.date, dt.date, str, list[Any]]:
    """Compute window (warmup_start, test_end_resolved, end_source, bars_flat)."""
    warmup_start = test_start - dt.timedelta(days=int(warmup_days))
    if test_end is not None:
        return warmup_start, test_end, "cli", []
    return warmup_start, test_start, "latest_bar", []


def write_run_json(
    schema: str,
    config: Mapping[str, Any],
    universe: Sequence[Any],
    coverage: Mapping[str, Any],
    run_result: Any,
    bars: Sequence[Any],
    out_dir: Path,
    *,
    paths: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute hashes and write run.json. Returns artifact dict."""
    from honba_examples.backtest import canonical_hash

    out_dir.mkdir(parents=True, exist_ok=True)
    if hasattr(run_result, "to_dict"):
        body = run_result.to_dict()
    elif hasattr(run_result, "as_dict"):
        body = run_result.as_dict()
    elif hasattr(run_result, "metrics"):
        body = {"metrics": run_result.metrics()}
    elif isinstance(run_result, dict):
        body = run_result
    else:
        body = str(run_result)

    u_symbols = [
        getattr(u, "symbol", u) if not isinstance(u, dict) else u.get("symbol", u)
        for u in universe
    ]
    b_digest = bars_digest(bars)
    input_hash = canonical_hash({"config": dict(config), "universe": u_symbols, "bars": b_digest})
    result_hash = canonical_hash(body)
    run_hash = canonical_hash([input_hash, result_hash])

    artifact: dict[str, Any] = {
        "schema": schema,
        "config": dict(config),
        "universe": u_symbols,
        "data_coverage": dict(coverage),
        "result": body,
        "input_hash": input_hash,
        "result_hash": result_hash,
        "run_hash": run_hash,
        "bars_digest": b_digest,
    }
    if paths is not None:
        artifact["paths"] = dict(paths)

    path = out_dir / "run.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=False, default=str) + "\n", encoding="utf-8")
    return artifact


