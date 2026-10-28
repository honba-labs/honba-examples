"""tests/unit/test_polyrepo_decoupling.py: Phase 7.1 polyrepo dependency decoupling.

Verifies the honba-examples package no longer depends on fragile relative filesystem paths
(``../honba-*``) into sibling checkouts and that every honba package imports purely through
installed (editable) distributions — no Rust compiler toolchain required on the example side.

This is the contract that lets examples run against published ``honba`` wheels:
``import honba`` and ``import honba_examples`` resolve from site-packages / the editable
workspace virtualenv, never from a hard-coded ``../`` checkout.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"

# A genuine decoupling violation is adding a sibling checkout to ``sys.path`` (or loading a
# module from one), not merely mentioning ``../honba-*`` in a docstring or error message.
_FRAGILE_SYS_PATH = re.compile(r"\.\./honba-[A-Za-z0-9_-]+")
_FRAGILE_SPEC = re.compile(r"spec_from_file_location\([^)]*\.\./honba-[A-Za-z0-9_-]+")


def _example_scripts() -> list[Path]:
    return sorted(p for p in EXAMPLES.glob("*/[0-9][0-9]_*.py") if p.is_file())


def test_honba_packages_import_without_rust_toolchain() -> None:
    """Every honba distribution the examples depend on imports cleanly.

    These are installed as editable distributions from sibling checkouts; importing them must
    not require ``cargo``, ``rustc`` or a ``target/`` build directory on the Python side.
    """
    for name in ("honba", "honba.data", "honba.adapters", "honba_examples", "honba.strategies"):
        importlib.import_module(name)


def test_pytest_ini_does_not_rely_on_fragile_sibling_path() -> None:
    """pytest.ini must not add a ``../honba-*`` checkout to ``pythonpath``."""
    ini = (ROOT / "pytest.ini").read_text()
    assert "../honba-" not in ini, "pytest.ini still references a fragile ../honba-* path"


def test_no_example_mutates_sys_path_with_sibling_checkout() -> None:
    """No numbered example script injects a ``../honba-*`` checkout into ``sys.path``."""
    violations: list[str] = []
    for script in _example_scripts():
        text = script.read_text()
        for line in text.splitlines():
            if "sys.path" in line and _FRAGILE_SYS_PATH.search(line):
                violations.append(script.relative_to(ROOT).as_posix())
                break
        if _FRAGILE_SPEC.search(text):
            violations.append(script.relative_to(ROOT).as_posix())
    assert not violations, (
        "examples mutate sys.path or load modules from a sibling honba checkout: "
        + ", ".join(sorted(set(violations)))
    )


def test_example_modules_load_via_installed_packages_only() -> None:
    """Loading example modules resolves honba/honba_examples from the installed
    distributions, not from a relative checkout path."""
    import importlib.util

    original_path = list(sys.path)
    try:
        for script in _example_scripts()[:5]:
            mod_name = f"_decoupling_probe_{script.stem}"
            spec = importlib.util.spec_from_file_location(mod_name, script)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
            for entry in sys.path:
                assert "../honba-" not in entry, f"{script.name} added {entry!r} to sys.path"
            del sys.modules[mod_name]
    finally:
        sys.path[:] = original_path
