from __future__ import annotations
import importlib
import sys
from pathlib import Path


def test_mutual_funds_03_imports():
    # add mutual_funds dir to path
    mf_dir = Path(__file__).resolve().parents[2] / "mutual_funds"
    if str(mf_dir) not in sys.path:
        sys.path.insert(0, str(mf_dir))
    mod = importlib.import_module("03_category_analysis")
    assert mod is not None
