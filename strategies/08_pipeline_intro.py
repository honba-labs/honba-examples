"""strategies/08_pipeline_intro: minimal pipeline DSL walkthrough.

The smallest recipe that shows the ``>>``/``|`` composition and context flow::

    pipeline = A() >> B() >> C()
    result = pipeline.run()

``A``, ``B`` and ``C`` are plain :class:`~honba_examples.pipeline.base.Step` objects.  This recipe
runs entirely in-process with a deterministic fake screener so it works with no Parquet
catalog and no network — the next recipes replace it with real screeners from
:mod:`honba_examples.pipeline.screeners`.

Run::

    python strategies/08_pipeline_intro.py
    python strategies/08_pipeline_intro.py --json

Pair with ``strategies/09_multibagger_momentum.py`` for a real-data version.

Pitfalls::

    - A hand-typed universe: ListUniverse returns RELIANCE and TCS whatever the date. In the real
      recipes the symbol list comes from a screen, and its validity depends entirely on the as-of
      date it ran on (see 09).
    - Order matters in a context pipeline: steps share one mutable dict, and a sizer computes
      weights from the symbols present when it runs. A filter placed after the sizer would leave
      weights that no longer match the surviving names.
    - Weights are not a result: equal 1/N weights on two names is 50 percent each, which hides
      concentration. There are no prices here, so nothing can be read as performance.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba_examples.cli import make_parser
from honba_examples.pipeline import EqualWeightSizer, Pipeline, Step

__all__ = ["main"]


class ListUniverse(Step):
    """Tiny universe / screener stub — puts two flags onto the context."""

    name = "list_universe"

    def __init__(self, symbols: list[str] | None = None) -> None:
        self.symbols_input = symbols or ["RELIANCE", "TCS"]

    def run(self, ctx: dict[str, Any]) -> dict[str, Any]:
        ctx["candidates"] = [{"symbol": s} for s in self.symbols_input]
        ctx["symbols"] = list(self.symbols_input)
        return ctx


class AnnotateStep(Step):
    """Copy / annotate the qualified symbols (shows the context flowing between steps)."""

    name = "annotate"

    def __init__(self, tag: str = "qualified") -> None:
        self.tag = tag

    def run(self, ctx: dict[str, Any]) -> dict[str, Any]:
        ctx["tag"] = self.tag
        return ctx


def build_pipeline() -> Pipeline:
    """Pipeline used by :func:`run` and the examples below."""
    return (
        ListUniverse(["RELIANCE", "TCS"])
        >> AnnotateStep("stage_2_candidates")
        >> EqualWeightSizer()
    )


def run() -> dict[str, Any]:
    """Drive the pipeline and return the final context."""
    return build_pipeline().run()


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--json", action="store_true", help="print the pipeline context as JSON")
    parser.parse_args(argv)
    ctx = run()
    payload: dict[str, Any] = {
        "symbols": ctx.get("symbols"),
        "candidates": ctx.get("candidates"),
        "weights": ctx.get("weights"),
        "tag": ctx.get("tag"),
    }
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
