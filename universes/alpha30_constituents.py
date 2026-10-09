"""alpha30_constituents: alias and compatibility shim for 03_alpha30_constituents.py.

Resolve Nifty200 Alpha 30, with a seed-list fallback.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ORIGIN = Path(__file__).resolve().parent / "03_alpha30_constituents.py"
_spec = importlib.util.spec_from_file_location("ex03_alpha30", _ORIGIN)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

NIFTY200_ALPHA_30_SEED = _mod.NIFTY200_ALPHA_30_SEED
UNIVERSE_NAME = _mod.UNIVERSE_NAME
register_alpha30_if_missing = _mod.register_alpha30_if_missing
load_alpha30 = _mod.load_alpha30
print_members = _mod.print_members
main = _mod.main

__all__ = [
    "NIFTY200_ALPHA_30_SEED",
    "UNIVERSE_NAME",
    "load_alpha30",
    "main",
    "print_members",
    "register_alpha30_if_missing",
]

if __name__ == "__main__":
    main()
