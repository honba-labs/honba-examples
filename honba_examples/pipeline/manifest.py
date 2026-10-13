"""honba_examples.pipeline.manifest: small builders for step manifests (``describe()``).

A manifest is a plain dict in the shape of honba-strategies' ``step.toml``
(``schema/step.schema.json``): the step's params, what it LOOKS AT (``requires``), what it needs
from the previous step (``consumes``) and what it produces (``emits``), including the columns it
may REPORT. These helpers only keep each ``describe()`` short; they add no behaviour.
"""

from __future__ import annotations

from typing import Any

__all__ = ["manifest", "param"]


def param(type: str, description: str, **fields: Any) -> dict[str, Any]:
    """One ``[params.<name>]`` table. ``fields`` are default/min/max/step/choices/pattern/..."""
    return {"type": type, **fields, "description": description}


def manifest(
    id: str,
    title: str,
    description: str,
    params: dict[str, dict[str, Any]],
    consumes: str,
    emits: str,
    *,
    requires: dict[str, Any] | None = None,
    columns: list[str] | None = None,
    adds: list[str] | None = None,
    reportable: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Assemble a manifest; ``consumes``/``emits`` are shapes, ``id`` is ``<kind>.<name>``."""
    out_emits: dict[str, Any] = {"shape": emits, "adds": list(adds or [])}
    out_emits["report"] = []
    out_emits["reportable"] = list(reportable or [])
    in_consumes: dict[str, Any] = {"shape": consumes}
    in_consumes["columns"] = list(columns if columns is not None else ["symbol"])
    return {
        "schema": 1,
        "id": id,
        "kind": id.split(".", 1)[0],
        "title": title,
        "description": description,
        "tags": list(tags or []),
        "params": params,
        "requires": requires or {},
        "consumes": in_consumes,
        "emits": out_emits,
    }
