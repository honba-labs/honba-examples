"""honba_examples.pipeline.chain: load, validate and run a chain of steps written as TOML.

A chain file lists steps by manifest ``id``; each may carry ``with`` (param overrides) and
``report`` (extra columns to show, which the step does not filter on)::

    [[steps]]
    uses = "screen.value"
    with = { asof = "2025-03-01", top = 10 }

    [[steps]]
    uses = "filter.fundamental_quality"
    with = { min_roce = 15.0 }
    report = ["market_cap", "dividend_yield_pct"]

``REGISTRY`` maps ids to the pipeline :class:`~honba_examples.pipeline.base.Step` classes (their
manifests come from ``describe()``). Steps of the honba-strategies catalog (``signal.*`` and the
self-contained ``execute.*`` portfolio strategies) are known to :func:`validate` by their
``step.toml`` but have no class here, so :func:`run_chain` refuses them with a clear error.
:func:`validate` checks a chain with honba-strategies'
``tools/validate_chain.py`` (shapes, columns, kind order, param types and bounds, ``report``
against ``emits.reportable``) after checking ids; :func:`run_chain` validates, builds the
:class:`~honba_examples.pipeline.base.Pipeline` and runs it.
"""

from __future__ import annotations

import datetime as dt
import inspect
from pathlib import Path
from typing import Any

import tomllib

from honba_examples.catalog import strategies_catalog_dir
from honba_examples.pipeline.base import Pipeline, PipelineResult, Step
from honba_examples.pipeline.execution import SipBacktestHarness
from honba_examples.pipeline.filters import (
    FundamentalQualityFilter,
    LiquidityFilter,
    MarketRegimeFilter,
)
from honba_examples.pipeline.screeners import (
    DividendYieldScreener,
    MultibaggerScreener,
    ValueScreener,
)
from honba_examples.pipeline.sizing import EqualWeightSizer, FixedSipSizer, InverseVolatilitySizer

__all__ = [
    "REGISTRY",
    "ChainError",
    "ChainNotRunnableError",
    "build_pipeline",
    "catalog_manifests",
    "describe_all",
    "load_chain",
    "run_chain",
    "validate",
]

_CLASSES: tuple[type[Step], ...] = (
    MultibaggerScreener,
    ValueScreener,
    DividendYieldScreener,
    FundamentalQualityFilter,
    MarketRegimeFilter,
    LiquidityFilter,
    FixedSipSizer,
    EqualWeightSizer,
    InverseVolatilitySizer,
    SipBacktestHarness,
)
REGISTRY: dict[str, type[Step]] = {cls.describe()["id"]: cls for cls in _CLASSES}
"""Step id (``<kind>.<name>``) to the Step class that implements it."""

# Params where a chain file writes 0 for "test disabled" (the constructors take None).
_ZERO_DISABLES = {"screen.value": ("max_pe", "max_pb")}
_STEP_KEYS = {"uses", "id", "with", "report"}


class ChainError(ValueError):
    """An invalid chain; ``errors`` holds every problem found."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("invalid chain:\n  " + "\n  ".join(errors))


class ChainNotRunnableError(ChainError):
    """A valid chain that uses honba-strategies catalog steps this pipeline cannot execute."""


def describe_all() -> dict[str, dict[str, Any]]:
    """Every registered step's manifest, by id."""
    return {sid: cls.describe() for sid, cls in REGISTRY.items()}


def _plain(value: Any) -> Any:
    """TOML dates become ISO strings, which is what ``date`` params and the steps accept."""
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def load_chain(source: str | Path | dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Read a chain from a TOML path, a ``{"steps": [...]}`` dict or a list of step dicts.

    Returns normalised steps ``{"uses": id, "with": {...}, "report": [...]}``. Structure
    errors raise :class:`ChainError`; whether the chain makes sense is :func:`validate`'s job.
    """
    if isinstance(source, (str, Path)):
        data: Any = tomllib.loads(Path(source).read_text())
    else:
        data = source
    raw = data.get("steps") if isinstance(data, dict) else data
    if not isinstance(raw, list) or not raw:
        raise ChainError(["chain needs a non-empty [[steps]] list"])
    steps: list[dict[str, Any]] = []
    errors: list[str] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            errors.append(f"step #{i}: expected a table, got {type(entry).__name__}")
            continue
        sid = entry.get("uses", entry.get("id"))
        if not isinstance(sid, str):
            errors.append(f"step #{i}: missing 'uses' (a step id such as 'screen.value')")
            continue
        for key in sorted(set(entry) - _STEP_KEYS):
            errors.append(f"{sid}: unknown key {key!r} (allowed: uses, with, report)")
        params, report = entry.get("with", {}), entry.get("report", [])
        if not isinstance(params, dict):
            errors.append(f"{sid}: 'with' must be a table")
            continue
        if not isinstance(report, list) or not all(isinstance(c, str) for c in report):
            errors.append(f"{sid}: 'report' must be a list of column names")
            continue
        steps.append({"uses": sid, "with": _plain(params), "report": list(report)})
    if errors:
        raise ChainError(errors)
    return steps


def catalog_manifests() -> dict[str, dict[str, Any]]:
    """The ``step.toml`` manifests of every honba-strategies catalog strategy, by step id."""
    import json

    root = strategies_catalog_dir()
    out: dict[str, dict[str, Any]] = {}
    for entry in json.loads((root / "registry.json").read_text())["strategies"]:
        path = root / entry["path"] / "step.toml"
        if path.is_file():
            manifest = tomllib.loads(path.read_text())
            out[manifest["id"]] = manifest
    return out


def _manifests(chain: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Manifests (with ``with``/``report`` attached) for the validator, plus unknown-id errors."""
    out: list[dict[str, Any]] = []
    errors: list[str] = []
    catalog: dict[str, dict[str, Any]] | None = None
    for step in chain:
        cls = REGISTRY.get(step["uses"])
        if cls is not None:
            base = cls.describe()
        else:
            catalog = catalog_manifests() if catalog is None else catalog
            if step["uses"] not in catalog:
                known = sorted(set(REGISTRY) | set(catalog))
                errors.append(f"unknown step id {step['uses']!r}; known ids are {known}")
                continue
            base = catalog[step["uses"]]
        out.append({**base, "with": step["with"], "report": step["report"]})
    return out, errors


def validate(chain: list[dict[str, Any]]) -> list[str]:
    """Errors for ``chain`` (empty when it is valid).

    Unknown ids are reported here; everything else (shape and column compatibility, kind order,
    param types, ``min``/``max``, ``choices``, ``pattern``, and ``report`` against
    ``emits.reportable``) comes from honba-strategies' ``validate_chain``. Raises
    ``honba.strategies.loader.CatalogError`` when that catalog cannot be found.
    """
    from honba_examples.catalog import load_catalog_tool

    tools = load_catalog_tool("validate_chain")  # fail on a missing catalog even if ids are bad
    try:
        chain = load_chain(chain)
    except ChainError as exc:
        return exc.errors
    manifests, errors = _manifests(chain)
    if errors:
        return errors
    return list(tools.validate_chain(manifests))


def _build_step(step: dict[str, Any], catalog_dir: Path | None) -> Step:
    sid, cls = step["uses"], REGISTRY[step["uses"]]
    kwargs = dict(step["with"])
    for name in _ZERO_DISABLES.get(sid, ()):
        if kwargs.get(name) == 0:
            kwargs[name] = None
    if step["report"]:
        kwargs["report"] = step["report"]
    if catalog_dir is not None and "catalog_dir" in inspect.signature(cls.__init__).parameters:
        kwargs.setdefault("catalog_dir", catalog_dir)
    return cls(**kwargs)


def build_pipeline(chain: list[dict[str, Any]], *, catalog_dir: Path | None = None) -> Pipeline:
    """Validate ``chain`` and build its :class:`Pipeline` (``catalog_dir`` overrides the data)."""
    errors = validate(chain)
    if errors:
        raise ChainError(errors)
    steps = load_chain(chain)
    foreign = [step["uses"] for step in steps if step["uses"] not in REGISTRY]
    if foreign:
        message = (
            f"{', '.join(foreign)}: validated but not runnable in this pipeline yet; "
            f"these are honba-strategies catalog steps and the pipeline only runs its own "
            f"steps ({', '.join(sorted(REGISTRY))}). Run catalog strategies with the honba "
            f"backtester instead."
        )
        raise ChainNotRunnableError([message])
    return Pipeline([_build_step(step, catalog_dir) for step in steps])


def run_chain(
    chain: list[dict[str, Any]],
    ctx: dict[str, Any] | None = None,
    *,
    catalog_dir: Path | None = None,
) -> PipelineResult:
    """Validate, build and run ``chain`` starting from ``ctx``."""
    return build_pipeline(chain, catalog_dir=catalog_dir).run(ctx)
