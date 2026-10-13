"""Tiny CLI helpers shared by the example scripts (parser, ``--out``, JSON emit)."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = ["add_out_arg", "emit_json", "make_parser", "run_example"]


def make_parser(doc: str | None) -> argparse.ArgumentParser:
    """Parser whose description is the first paragraph of ``doc``."""
    return argparse.ArgumentParser(description=(doc or "").split("\n\n")[0])


def add_out_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON here")


def emit_json(result: Any, out: Path | None) -> None:
    """Print ``result`` as indented JSON; also write it to ``out`` when given."""
    text = json.dumps(result, indent=2, default=str)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n")
    print(text)


def run_example(
    run: Callable[..., Any],
    doc: str | None,
    add_args: Callable[[argparse.ArgumentParser], None] | None = None,
    *,
    argv: list[str] | None = None,
    to_kwargs: Callable[[argparse.Namespace], dict[str, Any]] | None = None,
) -> int:
    """Parse ``argv``, call ``run(**kwargs)``, emit its result as JSON, return 0."""
    parser = make_parser(doc)
    if add_args:
        add_args(parser)
    add_out_arg(parser)
    args = parser.parse_args(argv)
    if to_kwargs:
        kwargs = to_kwargs(args)
    else:
        kwargs = {k: v for k, v in vars(args).items() if k != "out"}
    emit_json(run(**kwargs), args.out)
    return 0
