"""Every numbered example script documents its backtest/domain pitfalls.

Each ``<dir>/NN_*.py`` module docstring must carry a ``Pitfalls::`` block of 3 to 6 bullets
that are specific to what that script does (lookahead, survivorship, overfitting, costs, data
quality, synthetic-data caveats, ...). ``PENDING_PITFALLS`` (empty now) can stage a new script
whose section is still to come; remove its path in the same commit that adds the section.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIN_BULLETS = 3
MAX_BULLETS = 6

# Every numbered script now documents its pitfalls; add a path here only to stage a new script.
PENDING_PITFALLS: frozenset[str] = frozenset()


def _scripts() -> list[str]:
    return sorted(p.relative_to(ROOT).as_posix() for p in ROOT.glob("examples/*/[0-9][0-9]_*.py"))


def pitfall_bullets(doc: str | None) -> list[str] | None:
    """Bullets of the ``Pitfalls::`` block in ``doc`` (``None`` when there is no such block)."""
    lines = (doc or "").splitlines()
    for start, line in enumerate(lines):
        if line.strip() in ("Pitfalls::", "Pitfalls:"):
            header_indent = len(line) - len(line.lstrip())
            break
    else:
        return None
    bullets: list[str] = []
    for line in lines[start + 1 :]:
        if not line.strip():
            continue
        if len(line) - len(line.lstrip()) <= header_indent:
            break  # dedent: the block is over
        if line.lstrip().startswith("- "):
            bullets.append(line.strip())
    return bullets


def test_pitfall_parser_counts_bullets() -> None:
    doc = "Summary.\n\nPitfalls::\n\n    - one\n      wrapped\n    - two\n\nRun::\n\n    - not counted\n"
    assert pitfall_bullets(doc) == ["- one", "- two"]
    assert pitfall_bullets("Summary only.") is None


def test_scripts_are_discovered() -> None:
    assert len(_scripts()) >= 70


def test_pending_entries_exist() -> None:
    missing = sorted(set(PENDING_PITFALLS) - set(_scripts()))
    assert not missing, f"PENDING_PITFALLS names scripts that do not exist: {missing}"


@pytest.mark.parametrize("rel", _scripts())
def test_script_documents_pitfalls(rel: str) -> None:
    doc = ast.get_docstring(ast.parse((ROOT / rel).read_text()))
    bullets = pitfall_bullets(doc)
    if rel in PENDING_PITFALLS:
        pytest.skip("Pitfalls section pending")
    assert bullets is not None, f"{rel}: module docstring has no 'Pitfalls::' section"
    assert MIN_BULLETS <= len(bullets) <= MAX_BULLETS, (
        f"{rel}: expected {MIN_BULLETS}-{MAX_BULLETS} Pitfalls bullets, found {len(bullets)}"
    )
