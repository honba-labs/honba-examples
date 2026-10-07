# Refactor plan for `honba-examples`

Goal: shrink the largest examples (07 325→~150, 08 561→~300) by extracting duplicated plumbing into `honba_examples/`, keep all lesson logic intact, preserve existing tests, and do this incrementally with R1/TDD followed.

## 0. Principles & constraints

- **Test-preserving first.** Do not change test expectations unless a behavior change is intentional and necessary. Any such change is recorded in the commit message (R1).
- **Extract, don't erase lessons.** Move harness code (CLI, windowing, hashes/run.json, settlement, printing) into shared helpers; keep strategy/rebalance/math, coverage gates, and narrative docstrings in the example files.
- **Additive before destructive.** Create new helpers with unit tests first, switch examples to use them, only then delete in-file duplicates.
- **R1/TDD.** For any new behavior (e.g. `major_metrics`, label aliases, `write_run_json` shape), write a small unit test first (red), implement (green), refactor. Pure moves/copies need no new failing test.
- **One logical change per commit.** Commit after each subphase. Never `git add -A`.
- **Keep imports working.** Re-export symbols so `tests/unit/test_08_adds_sells.py` (which does `from universes import _08_alpha30_union_ewr_backtest as m` and calls `m.simulate`, `m.trade_changes`, ...) continues to work without test edits.

## 1. PHASE 0 — Create shared helpers (no behavior changes)

Create 3 new modules under `honba-examples/honba_examples/`. Add unit tests for each (deterministic). These are pure extractions.

### 1a. `honba_examples/settlement.py` (new)
**Absorbs:** 07:188–196 (`settlement precedence: cli > config > settlement_days_for`) + 08:493–508 (`engine_settlement_days` + `resolve_settlement_days`).  
**API:**

```python
from __future__ import annotations
from typing import Any, Tuple

def resolve_settlement_days(
    exchange: str | None,
    as_of,
    cli_value: int | None,
    cfg_value: Any,
) -> Tuple[int, str]:
    """Return (settlement_days, source) where source in {'cli','config','market'}."""
```

- Preserve precedence and return `source` string (07/08 both log/record it via config later).  
- **Tests (unit):** `tests/unit/test_settlement.py` (new). Cases: cli overrides, config overrides, market default; `None`/missing handled. (Red→Green.)

### 1b. `honba_examples/catalog.py` (new)
**Absorbs:** 07:171–177 (`find_catalog` + `load_catalog_strategy`).  
**API:**

```python
from __future__ import annotations
from pathlib import Path
from typing import Any

def load_named(name: str, strategies_dir: Path | None = None, search_from: Path | None = None) -> Any:
    """Load a named catalog strategy (honba.strategies.loader.load_catalog_strategy/find_catalog)."""
```

- Mirror existing behavior exactly (propagate exceptions as-is).  
- **Tests (unit):** `tests/unit/test_catalog.py` (new) — stub/mock `honba.strategies.loader` or use a tiny fixture (deterministic). Prefer unit with fakes (no network/filesystem I/O).

### 1c. `honba_examples/artifact.py` (new)
**Absorbs:** 07:76–84 (`bars_digest`), 07:255–273 (bars digest + `canonical_hash` twice + write `run.json` + prints), and window logic 07:197–210.  
**API:**

```python
from __future__ import annotations
import datetime as dt
from pathlib import Path
from typing import Any, Mapping, Sequence, Tuple

def bars_digest(bars: Sequence[Any]) -> str: ...

def resolve_window(
    test_start: dt.date,
    test_end: dt.date | None,
    warmup_days: int,
    load_fn,  # (inst_id, start, end) or callback
    *,
    require_full_coverage: bool = False,
) -> Tuple[dt.date, dt.date, str, list[Any]]:
    """Return (warmup_start, test_end_resolved, end_source, bars_flat)."""
```

Notes:
- Keep `data_coverage()` + `_gate_coverage()` (07:87–102, 212–225) inside `07_alpha30_backtest.py` (teaching points). Move only the date arithmetic/window resolution (197–210) into helpers as appropriate.
- **Tests (unit):** `tests/unit/test_artifact.py` (new).

### 1d. Move `tests/synthetic.py` → `honba_examples/synthetic.py` (shared test fixtures)
- Create `honba_examples/synthetic.py` (copy as-is). Keep `tests/synthetic.py` as a thin compatibility shim (re-export) for now.
- Update example imports where easy later (Phase 4).

### 1e. Fix `mutual_funds/03_category_analysis.py` import (bug fix, TDD)
- Add missing exports to `honba_examples/metrics.py` (additive) or compute via `curve_metrics`. Add regression test `tests/unit/test_mutual_funds_03_imports.py` (red→green).

## 2. PHASE 1 — Extend shared libraries

### 2a. `honba_examples/backtest.py` — add `major_metrics()`
Additive helper on `BacktestRun` returning major-unit metrics for printing. Unit test in `tests/unit/test_backtest.py`.

### 2b. `honba_examples/output.py` — label aliases + header passthrough
Add `_METRIC_LABEL_OVERRIDES` (map `n_fills`→"Fills", `n_released_unfunded`→"Buys cut for cash", `n_trades`→"Trades", etc.) and make `print_run_header` render arbitrary extra keys. Update/add unit tests.

## 3. PHASE 2 — Refactor `universes/07_alpha30_backtest.py` (325 → ~150)
Use `artifact`, `catalog`, `settlement`, `output`. Keep lesson logic (coverage gate, universe agreement). Integration test `test_07_alpha30_backtest.py` must remain green.

## 4. PHASE 3 — Extract 08 engine → `honba_examples/ewr.py` + correctness fix (561 → ~300)
- Create `ewr.py` relocating SimResult + rebalance/simulate verbatim (no semantic changes). Re-export all symbols.
- Refactor 08 to import from `ewr` and `settlement`/`output`. Replace 32-line `BASKET` with `resolve_universe("nifty200_alpha_30", exchange="NSE")` (correctness). 08 unit tests must remain green without edits.

## 5. PHASE 4 — Rollout (incremental)
De-duplicate `_registry`/`_parse_config`, SMA core, curve helpers, switch synthetic imports to `honba_examples.synthetic`, consider CLI tail helper.

## 6. PHASE 5 — Verification
Full unit/integration regression, ruff check, acceptance criteria as specified.

## Implementation notes
- Preserve `run.json` schema exactly (07). Include `settlement_source` in config.
- Keep coverage gate inside 07 (lesson). Re-export all 08 symbols from the script module for test imports. Label overrides align with existing table tests.
