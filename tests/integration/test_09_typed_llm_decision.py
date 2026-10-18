"""Integration test for ai_research/09_typed_llm_decision.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

EXAMPLE_PATH = (
    Path(__file__).resolve().parents[2] / "examples" / "ai_research" / "09_typed_llm_decision.py"
)


def test_09_typed_llm_decision_runs_cleanly() -> None:
    spec = importlib.util.spec_from_file_location("ex09_decision", EXAMPLE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Invoke run_typed_llm_decision_demo()
    assert hasattr(module, "run_typed_llm_decision_demo")
    module.run_typed_llm_decision_demo()
