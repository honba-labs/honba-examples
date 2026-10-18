"""tests/unit/test_production_ops.py: monitoring uptime and deployment file naming."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "examples"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "production" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_uptime_is_seconds_since_start_not_the_unix_clock() -> None:
    mod = _load("04_monitoring")
    line = next(
        ln for ln in mod.generate_metrics().splitlines() if ln.startswith("honba_uptime_seconds")
    )
    assert 0 <= int(line.split()[1]) < 3600  # the unix epoch clock is ~1.7e9


@pytest.mark.parametrize("name", ["../evil", "a/b", "", "with space", "x;rm -rf", "..", "-lead"])
def test_strategy_name_cannot_escape_the_output_directory(tmp_path, name) -> None:
    mod = _load("05_deployment")
    with pytest.raises(ValueError, match="strategy name"):
        mod.run(strategy_name=name, output=tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_valid_strategy_name_still_writes_the_files(tmp_path) -> None:
    mod = _load("05_deployment")
    out = mod.run(strategy_name="sma_crossover-2", output=tmp_path / "out")
    assert out["status"] == "written"
    assert (tmp_path / "out" / "honba-sma_crossover-2.service").is_file()


def test_dockerfile_cmd_points_at_the_config_the_script_writes(tmp_path) -> None:
    mod = _load("05_deployment")
    mod.run(strategy_name="momo", output=tmp_path)
    assert (tmp_path / "config" / "momo.toml").is_file()
    assert '"config/momo.toml"' in (tmp_path / "Dockerfile").read_text()
