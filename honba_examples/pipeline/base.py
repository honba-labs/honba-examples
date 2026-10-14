"""honba_examples.pipeline.base: composable in-code pipeline DSL primitives.

``Step`` is a single unit of work, ``Pipeline`` is an ordered sequence of steps, and both
compose with the overloaded ``>>`` and ``|`` operators::

    pipeline = Screener() >> Filter() >> Sizer() >> Harness()
    result = pipeline.run()

A plain mutable ``PipelineContext`` (a ``dict``) flows through every step; each step reads the
keys it needs and writes its outputs back onto the context before returning it.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from typing import Any, cast

from honba_examples.sip import sip_data_notes

__all__ = [
    "PipeMixin",
    "Pipeline",
    "PipelineContext",
    "PipelineResult",
    "Step",
    "as_date",
]

PipelineContext = dict[str, Any]
"""The mutable dict carried between pipeline steps."""


def as_date(value: str | dt.date | dt.datetime | None) -> dt.date | None:
    """Coerce a string / date-like value to a ``datetime.date`` (``None`` stays ``None``)."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))


class PipeMixin:
    """Composition operators shared by :class:`Step` and :class:`Pipeline`."""

    def __rshift__(self, other: Step | Pipeline) -> Pipeline:
        """Append ``other`` to this step/pipeline and return a :class:`Pipeline`."""
        pipeline = self if isinstance(self, Pipeline) else Pipeline([cast("Step", self)])
        return pipeline.append(other)

    def __or__(self, other: Step | Pipeline) -> Pipeline:
        """Alias for ``>>`` — ``|`` reads as a shell-style pipe between steps."""
        return self.__rshift__(other)


class Step(PipeMixin, ABC):
    """A single composable pipeline stage transforming a :data:`PipelineContext`."""

    name: str = "step"

    @abstractmethod
    def run(self, ctx: PipelineContext) -> PipelineContext:
        """Transform ``ctx`` and return the updated context."""

    def __call__(self, ctx: PipelineContext) -> PipelineContext:
        return self.run(ctx)

    @classmethod
    def describe(cls) -> dict[str, Any]:
        """The step's manifest (``step.toml`` shape); steps without one raise ``NotImplementedError``."""
        raise NotImplementedError(f"{cls.__name__} has no manifest")


class Pipeline(PipeMixin):
    """An ordered, runnable sequence of :class:`Step` instances."""

    def __init__(self, steps: list[Step | Pipeline] | None = None) -> None:
        self.steps: list[Step] = []
        for step in steps or []:
            self.append(step)

    def append(self, other: Step | Pipeline) -> Pipeline:
        """Append a step (or flatten another pipeline) and return ``self`` for chaining."""
        if isinstance(other, Pipeline):
            self.steps.extend(other.steps)
        elif isinstance(other, Step):
            self.steps.append(other)
        else:
            raise TypeError(f"Pipeline steps must be Step instances, got {type(other).__name__}")
        return self

    def __len__(self) -> int:
        return len(self.steps)

    def __iter__(self) -> Iterator[Step]:
        return iter(self.steps)

    def run(self, initial_ctx: Mapping[str, Any] | None = None) -> PipelineResult:
        """Drive every step in order, returning a :class:`PipelineResult` context."""
        ctx: PipelineContext = dict(initial_ctx or {})
        for step in self.steps:
            ctx = step.run(ctx) or {}
        return PipelineResult(ctx)


class PipelineResult(PipelineContext):
    """Context returned by :meth:`Pipeline.run` with convenient accessors."""

    @property
    def summary(self) -> dict[str, Any] | None:
        value = self.get("summary")
        return value if isinstance(value, dict) else None

    @property
    def stocks(self) -> list[dict[str, Any]]:
        value = self.get("stocks")
        return list(value) if isinstance(value, list) else []

    @property
    def candidates(self) -> list[dict[str, Any]]:
        value = self.get("candidates")
        return list(value) if isinstance(value, list) else []

    @property
    def symbols(self) -> list[str]:
        value = self.get("symbols")
        return [str(s) for s in value] if isinstance(value, list) else []

    @property
    def weights(self) -> dict[str, float]:
        value = self.get("weights")
        return dict(value) if isinstance(value, dict) else {}

    def print_summary(self) -> None:
        """Print the portfolio summary table (Rich when available, plain text otherwise)."""
        summary = self.summary or {}
        stocks = self.stocks
        try:
            from rich import box
            from rich.console import Console
            from rich.table import Table
        except ImportError:  # pragma: no cover - Rich is a declared dependency
            self._print_plain_summary(summary, stocks)
            return

        console = Console()
        if stocks:
            table = Table(
                title="Pipeline Backtest — Stock Breakdown",
                box=box.ROUNDED,
                header_style="bold cyan",
            )
            table.add_column("Symbol", style="bold cyan")
            table.add_column("Weight %", justify="right")
            table.add_column("Invested", justify="right")
            table.add_column("Final Value", justify="right", style="bold green")
            table.add_column("Profit", justify="right")
            table.add_column("XIRR %", justify="right", style="magenta")
            for s in stocks:
                xirr = s.get("xirr_pct")
                table.add_row(
                    str(s.get("symbol", "")),
                    f"{float(s.get('weight_pct', 0.0)):.1f}",
                    f"{float(s.get('total_invested', 0.0)):,.0f}",
                    f"{float(s.get('final_val', 0.0)):,.0f}",
                    f"{float(s.get('profit', 0.0)):+,.0f}",
                    f"{float(xirr):+.1f}" if xirr is not None else "N/A",
                )
            console.print(table)
            for note in sip_data_notes(summary):
                console.print(f"[yellow]⚠ {note}[/yellow]")

        if summary:
            xirr = summary.get("portfolio_xirr")
            xirr_str = f"{float(xirr):+.1f}%" if xirr is not None else "N/A"
            console.print(
                f"Invested ₹{float(summary.get('total_invested', 0.0)):,.0f} | "
                f"Value ₹{float(summary.get('total_value', 0.0)):,.0f} | "
                f"Profit ₹{float(summary.get('total_profit', 0.0)):+,.0f} | "
                f"Multiple {float(summary.get('portfolio_multiple', 1.0)):.2f}x | "
                f"XIRR {xirr_str}"
            )

    @staticmethod
    def _print_plain_summary(summary: dict[str, Any], stocks: list[dict[str, Any]]) -> None:
        for note in sip_data_notes(summary):
            print(f"warning: {note}")
        for s in stocks:
            print(
                f"{s.get('symbol', ''):<12} invested={float(s.get('total_invested', 0.0)):,.0f} "
                f"value={float(s.get('final_val', 0.0)):,.0f} "
                f"profit={float(s.get('profit', 0.0)):+,.0f}"
            )
        if summary:
            print(
                f"total_invested={float(summary.get('total_invested', 0.0)):,.0f} "
                f"total_value={float(summary.get('total_value', 0.0)):,.0f} "
                f"portfolio_multiple={float(summary.get('portfolio_multiple', 1.0)):.2f}x"
            )
