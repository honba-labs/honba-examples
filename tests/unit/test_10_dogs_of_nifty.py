"""strategies/10_dogs_of_nifty: the CLI flags reach the pipeline builder."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "strategies" / "10_dogs_of_nifty.py"


@pytest.fixture
def example():
    spec = importlib.util.spec_from_file_location("dogs_of_nifty", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakePipeline:
    def run(self) -> dict:
        return {}


def test_benchmark_and_end_date_flags_reach_build_pipeline(example, monkeypatch, capsys):
    seen: dict = {}

    def fake_build(**kwargs):
        seen.update(kwargs)
        return _FakePipeline()

    monkeypatch.setattr(example, "build_pipeline", fake_build)
    argv = ["--benchmark", "NIFTY 50", "--end-date", "2025-09-01", "--format", "json"]
    assert example.main(argv) == 0
    assert seen["benchmark"] == "NIFTY 50"
    assert seen["end_date"] == "2025-09-01"
    capsys.readouterr()


def test_regime_filter_is_in_the_chain_only_with_a_benchmark(example):
    names = lambda p: [type(s).__name__ for s in p.steps]
    assert "MarketRegimeFilter" not in names(example.build_pipeline())
    assert "MarketRegimeFilter" in names(example.build_pipeline(benchmark="NIFTY 50"))
